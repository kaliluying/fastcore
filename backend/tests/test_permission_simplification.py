import ast
import json
import re
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api.v1.module_system.role import crud as role_crud
from app.api.v1.module_system.role.crud import RoleCRUD
from app.api.v1.module_system.role.schema import RoleOutSchema
from app.api.v1.module_system.user import crud as user_crud
from app.api.v1.module_system.user import service as user_service
from app.api.v1.module_system.user.crud import UserCRUD
from app.api.v1.module_system.user.schema import UserUpdateSchema
from app.core.base_schema import AuthSchema
from app.core.dependencies import _load_user_from_db
from app.core.exceptions import CustomException
from app.core.permission_catalog import PERMISSION_CODES
from app.scripts.initialize import get_seed_data

SEED_DIR = Path(__file__).resolve().parents[1] / "app" / "scripts" / "data"
APP_DIR = Path(__file__).resolve().parents[1] / "app"


def test_role_schema_rejects_legacy_department_data_scope() -> None:
    """Legacy department data scopes (e.g. 2, 3) must be rejected."""
    with pytest.raises(ValidationError):
        RoleOutSchema(name="旧角色", code="LEGACY_ROLE", data_scope=3)


class _FakeScalarResult:
    def __init__(self, user):
        self._user = user

    def first(self):
        return self._user


class _FakeExecuteResult:
    def __init__(self, user):
        self._user = user

    def scalars(self):
        return _FakeScalarResult(self._user)


class _FakeDb:
    def __init__(self, user):
        self._user = user

    async def execute(self, _stmt):
        return _FakeExecuteResult(self._user)


def _menu(**overrides):
    data = {
        "id": 1,
        "name": "用户管理",
        "type": 2,
        "order": 1,
        "permission": "module_system:user:query",
        "route_name": "User",
        "route_path": "user",
        "component_path": "module_system/user/index",
        "title": "用户管理",
        "status": 0,
        "parent_id": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _role(**overrides):
    data = {
        "id": 1,
        "name": "普通用户",
        "code": "USER",
        "order": 1,
        "status": 0,
        "data_scope": 1,
        "menus": [],
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _user(**overrides):
    data = {
        "id": 1,
        "username": "user",
        "name": "普通用户",
        "mobile": None,
        "email": None,
        "gender": "2",
        "avatar": None,
        "is_superuser": False,
        "roles": [],
        "status": 0,
        "description": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_load_user_from_db_preserves_role_relationships():
    enabled_role = _role(id=1, code="USER", status=0)
    disabled_role = _role(id=2, code="DISABLED", status=1)
    user = _user(roles=[enabled_role, disabled_role])

    loaded = await _load_user_from_db(_FakeDb(user), "user")

    assert loaded.roles == [enabled_role, disabled_role]


@pytest.mark.asyncio
@pytest.mark.parametrize("is_superuser", [False, True])
@pytest.mark.parametrize("empty_menus", [False, True])
async def test_current_info_returns_flat_permissions_from_enabled_role_menus(monkeypatch, is_superuser, empty_menus):
    root_menu = _menu(
        id=9, name="系统管理", type=1, permission=None, component_path=None,
        route_name="System", route_path="/system", redirect="/system/user",
    )
    page_menu = _menu(
        id=10,
        parent_id=9,
        permission="module_system:user:query",
        route_name="User",
        route_path="user",
        component_path="module_system/user/index",
    )
    button_menu = _menu(
        id=11,
        name="新增",
        title="新增",
        type=3,
        order=1,
        permission="module_system:user:create",
        route_name=None,
        route_path=None,
        component_path=None,
        parent_id=10,
    )
    disabled_menu = _menu(
        id=12,
        name="停用",
        title="停用",
        type=3,
        order=2,
        permission="module_system:user:delete",
        status=1,
        route_name=None,
        route_path=None,
        component_path=None,
        parent_id=10,
    )
    role = _role(menus=[] if empty_menus else [root_menu, page_menu, button_menu, disabled_menu])
    current_user = _user(roles=[role], is_superuser=is_superuser)

    class FakeUserCRUD:
        def __init__(self, _auth):
            pass

        async def get(self, id):
            assert id == current_user.id
            return current_user

    class FakeMenuCRUD:
        def __init__(self, _auth):
            pass

        async def tree_list(self, **_kwargs):
            return [] if empty_menus else [root_menu, page_menu, button_menu]

    monkeypatch.setattr(user_service, "UserCRUD", FakeUserCRUD)
    monkeypatch.setattr(user_service, "MenuCRUD", FakeMenuCRUD)

    result = await user_service.UserService(AuthSchema(user=current_user)).current_info()

    expected_permissions = [] if empty_menus else [
        "module_system:user:create",
        "module_system:user:query",
    ]
    assert result.permissions == (["*:*:*"] if is_superuser else expected_permissions)
    payload = result.model_dump(mode="json", warnings="error")
    if empty_menus:
        assert payload["menus"] == []
    else:
        assert [menu["id"] for menu in payload["menus"]] == [9]
        assert [menu["id"] for menu in payload["menus"][0]["children"]] == [10]
        buttons = payload["menus"][0]["children"][0]["children"]
        assert [menu["id"] for menu in buttons] == [11]
        assert buttons[0]["children"] is None


@pytest.mark.asyncio
async def test_current_info_queries_only_current_menu_columns(monkeypatch):
    menu = _menu()
    disabled_menu = _menu(id=2, permission="module_system:user:delete")
    current_user = _user(roles=[_role(menus=[menu]), _role(id=2, status=1, menus=[disabled_menu])])
    searches = []

    class FakeUserCRUD:
        def __init__(self, _auth):
            pass

        async def get(self, id):
            assert id == current_user.id
            return current_user

    class FakeMenuCRUD:
        def __init__(self, _auth):
            pass

        async def tree_list(self, **kwargs):
            searches.append(kwargs["search"])
            return [menu]

    monkeypatch.setattr(user_service, "UserCRUD", FakeUserCRUD)
    monkeypatch.setattr(user_service, "MenuCRUD", FakeMenuCRUD)

    await user_service.UserService(AuthSchema(user=current_user)).current_info()

    assert searches == [{"id": ("in", [menu.id])}]
    assert all("client" not in search and "scope" not in search for search in searches)


@pytest.mark.asyncio
async def test_set_user_roles_rejects_unknown_roles_without_changing_relationships(monkeypatch):
    user = SimpleNamespace(id=1, roles=[_role()])
    crud = object.__new__(UserCRUD)
    crud.auth = SimpleNamespace(db=SimpleNamespace(flush=AsyncMock()))

    async def get_list(*, search):
        assert search == {"id": ("in", [user.id])}
        return [user]

    class FakeRoleCRUD:
        def __init__(self, _auth):
            pass

        async def get_list(self, *, search):
            assert search == {"id": ("in", [999])}
            return []

    monkeypatch.setattr(crud, "get_list", get_list)
    monkeypatch.setattr(user_crud, "RoleCRUD", FakeRoleCRUD)

    with pytest.raises(CustomException, match="部分角色不存在"):
        await crud.set_user_roles(user_ids=[user.id], role_ids=[999])

    assert user.roles == [_role()]


@pytest.mark.asyncio
async def test_set_role_menus_rejects_unknown_menus_without_changing_relationships(monkeypatch):
    existing_menu = _menu()
    role = _role(menus=[existing_menu])
    crud = object.__new__(RoleCRUD)
    crud.auth = SimpleNamespace(db=SimpleNamespace(flush=AsyncMock()))

    async def get_list(*, search):
        assert search == {"id": ("in", [role.id])}
        return [role]

    class FakeMenuCRUD:
        def __init__(self, _auth):
            pass

        async def get_list(self, *, search):
            assert search == {"id": ("in", [999])}
            return []

    monkeypatch.setattr(crud, "get_list", get_list)
    monkeypatch.setattr(role_crud, "MenuCRUD", FakeMenuCRUD)

    with pytest.raises(CustomException, match="部分菜单不存在"):
        await crud.set_role_menus_crud(role_ids=[role.id], menu_ids=[999])

    assert role.menus == [existing_menu]


@pytest.mark.asyncio
async def test_update_user_clears_roles_when_an_empty_list_is_submitted(monkeypatch):
    target_user = _user(id=2, username="target", roles=[_role()])
    set_role_calls = []

    class FakeUserCRUD:
        def __init__(self, _auth):
            pass

        async def get_or_404(self, *, id):
            assert id == target_user.id
            return target_user

        async def get(self, *, username):
            assert username == target_user.username
            return target_user

        async def update(self, *, id, data):
            assert id == target_user.id
            assert data.role_ids == []
            return target_user

        async def set_user_roles(self, *, user_ids, role_ids):
            set_role_calls.append((user_ids, role_ids))

    monkeypatch.setattr(user_service, "UserCRUD", FakeUserCRUD)

    result = await user_service.UserService(AuthSchema(user=_user(is_superuser=True))).update(
        id=target_user.id,
        data=UserUpdateSchema(username=target_user.username, role_ids=[]),
    )

    assert result.username == target_user.username
    assert set_role_calls == [([target_user.id], [])]


def test_seed_roles_are_single_org_baseline_only():
    roles = json.loads((SEED_DIR / "sys_role.json").read_text(encoding="utf-8"))

    assert [role["code"] for role in roles] == ["SUPER_ADMIN", "ADMIN", "USER"]
    assert not any("tenant_id" in role for role in roles)
    assert not any(role["code"].startswith(("STAR_", "INNO_")) for role in roles)


def test_seed_users_only_include_baseline_accounts():
    users = json.loads((SEED_DIR / "sys_user.json").read_text(encoding="utf-8"))
    user_roles = json.loads((SEED_DIR / "sys_user_roles.json").read_text(encoding="utf-8"))
    login_logs = json.loads((SEED_DIR / "sys_login_log.json").read_text(encoding="utf-8"))

    assert [user["username"] for user in users] == ["super", "admin", "user"]
    assert not any("tenant_id" in user for user in users)
    assert {item["user_id"] for item in user_roles} == set(range(1, len(users) + 1))
    assert {item["role_id"] for item in user_roles} <= {1, 2, 3}
    assert not {"product", "hr"} & {item["username"] for item in login_logs}


def test_initialized_user_list_only_contains_baseline_accounts(test_client, auth_headers):
    response = test_client.get("/system/user/list", params={"page_no": 1, "page_size": 20}, headers=auth_headers)

    assert response.status_code == 200
    assert {item["username"] for item in response.json()["data"]["items"]} == {"super", "admin", "user"}


def test_seed_role_menu_mapping_grants_user_ai_session_access():
    role_menus = get_seed_data("sys_role_menus")

    assert role_menus
    assert {item["role_code"] for item in role_menus} == {"USER"}
    assert {item.get("permission") for item in role_menus if item.get("permission")} >= {
        "module_ai:chat:query",
        "module_ai:chat:ws",
        "module_ai:session:query",
    }


@pytest.mark.asyncio
async def test_change_password_persists_the_hash(monkeypatch):
    user = SimpleNamespace(id=1, password="old", updated_id=None)
    db = SimpleNamespace(flush=AsyncMock(), refresh=AsyncMock())
    crud = object.__new__(UserCRUD)
    crud.auth = SimpleNamespace(user=SimpleNamespace(id=99), db=db)

    async def get_or_404(*, id):
        assert id == user.id
        return user

    monkeypatch.setattr(crud, "get_or_404", get_or_404)

    result = await crud.change_password(id=user.id, password_hash="new-hash")

    assert result is user
    assert user.password == "new-hash"
    assert user.updated_id == 99
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(user)


def test_seed_data_has_no_historical_org_noise():
    historical_keywords = ("STAR_", "INNO_", "星辰", "创新")

    def walk(value):
        if isinstance(value, dict):
            assert "tenant_id" not in value
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for path in SEED_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert not any(keyword in text for keyword in historical_keywords), path.name
        walk(json.loads(text))


def test_seed_buttons_are_permission_resources_not_routes():
    menus = get_seed_data("platform_menu")
    buttons = []

    def walk(nodes):
        for node in nodes:
            if node.get("type") == 3:
                buttons.append(node)
            walk(node.get("children") or [])

    walk(menus)

    assert buttons
    assert all(button.get("permission") for button in buttons)
    assert all(not button.get("route_path") for button in buttons)
    assert all(not button.get("route_name") for button in buttons)
    assert all(not button.get("component_path") for button in buttons)


def test_permission_codes_are_scoped_and_only_reused_within_same_page():
    menus = get_seed_data("platform_menu")
    permissions_by_code = defaultdict(list)

    def walk(nodes, page_key: str | None = None):
        for node in nodes:
            current_page_key = page_key
            if node.get("type") == 2:
                current_page_key = node.get("route_name") or node.get("route_path") or node.get("name")
            if node.get("permission"):
                permissions_by_code[node["permission"]].append(
                    {
                        "type": node.get("type"),
                        "page_key": current_page_key,
                        "name": node.get("name"),
                    }
                )
            walk(node.get("children") or [], current_page_key)

    walk(menus)

    assert all(
        permission.startswith(("module_system:", "module_platform:", "module_ai:", "module_common:", "module_demo:"))
        for permission in permissions_by_code
    )

    duplicates = {code: entries for code, entries in permissions_by_code.items() if len(entries) > 1}
    assert duplicates
    assert all(len({entry["page_key"] for entry in entries}) == 1 for entries in duplicates.values())
    assert all({entry["type"] for entry in entries} <= {2, 3} for entries in duplicates.values())


def test_permission_catalog_has_no_removed_department_permissions():
    assert not any(":dept:" in permission for permission in PERMISSION_CODES)


def test_menu_permissions_are_declared_in_permission_catalog():
    menus = get_seed_data("platform_menu")
    menu_permissions = set()

    def walk(nodes):
        for node in nodes:
            if node.get("permission"):
                menu_permissions.add(node["permission"])
            walk(node.get("children") or [])

    walk(menus)

    missing = sorted(menu_permissions - PERMISSION_CODES)
    assert not missing


def test_backend_permission_dependencies_are_declared_in_permission_catalog():
    used_permissions = set()
    for path in APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "permission_catalog.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "AuthPermission":
                continue
            if not node.args or not isinstance(node.args[0], ast.List):
                continue
            for item in node.args[0].elts:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    used_permissions.add(item.value)

    missing = sorted(used_permissions - PERMISSION_CODES)
    assert not missing


def test_chat_http_routes_use_explicit_permission_dependencies():
    controller = (APP_DIR / "plugin" / "module_ai" / "chat" / "controller.py").read_text(encoding="utf-8")

    assert not re.search(r"Depends\(get_current_user\)", controller)
    for permission in [
        "module_ai:session:detail",
        "module_ai:session:query",
        "module_ai:chat:create",
        "module_ai:chat:update",
        "module_ai:session:delete",
        "module_ai:chat:ws",
        "module_ai:model_config:query",
        "module_ai:model_config:update",
    ]:
        assert permission in controller
