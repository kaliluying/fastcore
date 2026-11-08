"""Exercise the audit defects through real persistence and request boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import APIRouter, BackgroundTasks, FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_system.auth import oauth_service
from app.api.v1.module_system.role.model import RoleModel
from app.api.v1.module_system.role.service import RoleService
from app.api.v1.module_system.user.model import UserModel, UserRolesModel
from app.common.enums import RedisInitKeyConfig
from app.core import dependencies, router_class
from app.core.base_model import MappedBase
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.core.http_limit import http_limit_callback
from app.core.permission import Permission
from app.core.redis_crud import RedisCURD
from app.core.security import credential_fingerprint
from app.plugin.module_ai.chat.crud import ChatSessionCRUD
from app.plugin.module_ai.chat.schema import ChatSessionCreateSchema
from app.plugin.module_ai.knowledge.bm25_index import BM25KnowledgeIndex
from tests.test_auth_session import FakeRedis


@pytest.fixture
async def audit_sessions(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(MappedBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(dependencies, "async_db_session", sessions)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def seed_user(sessions, *, status=0):
    async with sessions() as db:
        menu = MenuModel(name="Audit chat", type=3, permission="module_ai:chat:ws", status=0)
        role = RoleModel(id=9, name="Audit role", code="AUDIT", status=status, data_scope=4, menus=[menu])
        user = UserModel(username="audit", name="Audit", password="fixture", status=0, roles=[role])
        db.add(user)
        await db.commit()
        return user.id, role.id, menu.id


async def test_authentication_preserves_disabled_role_bindings(audit_sessions):
    user_id, _, _ = await seed_user(audit_sessions, status=1)
    async with audit_sessions() as db:
        user = await dependencies._load_user_from_db(db, "audit")
        assert dependencies._build_permission_map(user) == {}
        await db.commit()
    async with audit_sessions() as db:
        count = await db.scalar(select(func.count()).select_from(UserRolesModel).where(UserRolesModel.user_id == user_id))
        assert count == 1


@pytest.mark.parametrize("deleted", ["role", "menu"])
async def test_soft_deleted_authorization_is_not_granted(audit_sessions, deleted):
    user_id, role_id, menu_id = await seed_user(audit_sessions)
    async with audit_sessions() as db:
        auth = AuthSchema(db=db, user=SimpleNamespace(id=user_id, is_superuser=True), check_data_scope=False)
        if deleted == "role":
            await RoleService(auth).delete([role_id])
        else:
            menu = await db.get(MenuModel, menu_id)
            menu.is_deleted = True
        await db.commit()
    async with audit_sessions() as db:
        user = await dependencies._load_user_from_db(db, "audit")
        assert dependencies._build_permission_map(user) == {}
        auth = AuthSchema(db=db, user=user)
        condition = await Permission(MenuModel, auth).permission_condition()
        assert not (await db.scalars(select(MenuModel).where(condition))).all()
        if deleted == "role":
            from app.api.v1.module_system.log.model import OperationLogModel

            condition = await Permission(OperationLogModel, auth).permission_condition()
            assert condition is not None  # A deleted ALL role must not grant all data.


async def test_operation_log_preserves_business_background_tasks(monkeypatch):
    monkeypatch.setattr(router_class.settings, "OPERATION_LOG_RECORD", True)
    monkeypatch.setattr(router_class.settings, "OPERATION_RECORD_METHOD", ["POST"])
    calls = []

    async def audit(_record):
        calls.append("audit")

    monkeypatch.setattr(router_class, "_write_operation_log_async", audit)
    router = APIRouter(route_class=router_class.OperationLogRoute)

    @router.post("/task")
    async def respond(tasks: BackgroundTasks):
        tasks.add_task(calls.append, "business")
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/task")).status_code == 200
    assert calls == ["business", "audit"]


@pytest.mark.parametrize("milliseconds,seconds", [(0, 0), (1, 1), (2000, 2), (2001, 3)])
async def test_rate_limit_wait_is_in_seconds(milliseconds, seconds):
    with pytest.raises(CustomException) as exc:
        http_limit_callback(None, None, milliseconds)
    assert exc.value.data["Retry-After"] == str(seconds)


async def test_concurrent_chat_appends_keep_both_answers(audit_sessions):
    user = SimpleNamespace(id=1)
    async with audit_sessions() as db:
        session = await ChatSessionCRUD(AuthSchema(db=db, user=user)).create_crud(ChatSessionCreateSchema(title="Audit"))
        await db.commit()
    async with audit_sessions() as a, audit_sessions() as b:
        first = ChatSessionCRUD(AuthSchema(db=a, user=user))
        second = ChatSessionCRUD(AuthSchema(db=b, user=user))
        # Keep both stale ORM instances alive, as two chat requests do.
        stale = [await first._get_model(session.session_id), await second._get_model(session.session_id)]
        assert stale[0].runs == stale[1].runs == []

        async def append(crud, label):
            assert await crud.append_run_crud(session_id=session.session_id, message=label, response=label)
            await crud.db.commit()

        await asyncio.gather(append(first, "A"), append(second, "B"))
    async with audit_sessions() as db:
        saved = await ChatSessionCRUD(AuthSchema(db=db, user=user)).get_by_id_crud(session.session_id)
        assert {run["content"] for run in saved.runs} == {"A", "B"}
        assert len({run["run_id"] for run in saved.runs}) == 2


async def test_bm25_scope_is_applied_before_result_limit(tmp_path):
    index = BM25KnowledgeIndex(index_dir=str(tmp_path), tokenizer="char")
    await index.add_chunks([
        {"id": f"other-{i}", "content": "needle", "knowledge_base_id": 2, "document_id": 2}
        for i in range(45)
    ] + [{"id": "wanted", "content": "needle " + "filler " * 500, "knowledge_base_id": 1, "document_id": 1}])
    hits = await index.search("needle", [1], top_k=20)
    assert [hit["chunk_id"] for hit in hits] == ["wanted"]
    assert await index.search("needle", []) == []


@pytest.mark.parametrize("operation", ["add", "delete_document", "delete_chunks"])
async def test_bm25_writes_wait_for_shared_lock(tmp_path, operation):
    index = BM25KnowledgeIndex(index_dir=str(tmp_path), tokenizer="char")
    chunk = {"id": "wanted", "content": "needle", "knowledge_base_id": 1, "document_id": 1}
    await index.add_chunks([chunk])
    writer = index._get_index().writer()
    operations = {
        "add": lambda: index.add_chunks([{**chunk, "id": "new"}]),
        "delete_document": lambda: index.delete_by_document(1),
        "delete_chunks": lambda: index.delete_chunks(["wanted"]),
    }
    task = asyncio.create_task(operations[operation]())
    try:
        await asyncio.sleep(0.2)
        assert not task.done()
    finally:
        writer.cancel()
    await asyncio.wait_for(task, timeout=5)


async def online_session(redis, user_id):
    session_id = "audit-session"
    await RedisCURD(redis).set(
        f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}",
        {"session_id": session_id, "user_name": "audit", "user_id": user_id, "credential_fingerprint": credential_fingerprint(user_id, "fixture")}, expire=60,
    )
    await RedisCURD(redis).set(f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}", "fixture", expire=60)
    return session_id


async def test_permission_revocation_cannot_repopulate_stale_cache(audit_sessions):
    from sqlalchemy import update

    user_id, _, menu_id = await seed_user(audit_sessions)
    redis = FakeRedis()
    session_id = await online_session(redis, user_id)
    # An old deployment's cache entries must never grant permissions now.
    await RedisCURD(redis).set("auth:permissions:version", "1")
    await RedisCURD(redis).set(f"auth:permissions:1:{user_id}", {"module_ai:chat:ws": menu_id}, expire=300)
    async with audit_sessions() as writer:
        await writer.execute(update(MenuModel).where(MenuModel.id == menu_id).values(status=1))
        async with audit_sessions() as reader:
            before = await dependencies._verify_session(session_id, reader, redis)
            assert "module_ai:chat:ws" in before.permission_map
        await writer.commit()
    async with audit_sessions() as reader:
        after = await dependencies._verify_session(session_id, reader, redis)
        assert after.permission_map == {}


@pytest.mark.parametrize("provider", ["github", "gitee", "wechat", "qq"])
async def test_bound_oauth_login_keeps_user_and_commits_timestamp(audit_sessions, monkeypatch, provider):
    user_id, _, _ = await seed_user(audit_sessions)
    redis = FakeRedis()
    await RedisCURD(redis).set(oauth_service.STATE_PREFIX + "state", {"provider": provider, "frontend_redirect": "https://fixture.invalid/login"})
    monkeypatch.setattr(oauth_service, "_callback_url", lambda *_: "https://fixture.invalid/callback")
    monkeypatch.setattr(oauth_service, "_require_credentials", lambda *_: ("fixture", "fixture"))
    monkeypatch.setattr(oauth_service, f"exchange_{provider}_token", AsyncMock(return_value="fixture" if provider in {"github", "gitee"} else ("fixture", "uid")))
    monkeypatch.setattr(oauth_service, f"fetch_{provider}_profile", AsyncMock(return_value=("uid", "Audit", None) if provider in {"github", "gitee"} else ("uid", "Audit")))
    async with audit_sessions() as db:
        user = await db.get(UserModel, user_id)
        monkeypatch.setattr(oauth_service, "ensure_oauth_user", AsyncMock(return_value=user))
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"), state=SimpleNamespace())
        token, frontend = await oauth_service.complete_oauth_login(request=request, redis=redis, db=db, provider=provider, code="fixture", state="state")
        assert token.access_token and frontend == "https://fixture.invalid/login"
        await db.rollback()  # GET dependency cleanup must not undo the login timestamp.
    async with audit_sessions() as db:
        assert (await db.get(UserModel, user_id)).last_login is not None
