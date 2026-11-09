"""Regression tests for the P1/P2 security and data-isolation fixes."""

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Integer, String, event, select
from sqlalchemy.exc import CompileError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from starlette.datastructures import UploadFile
from starlette.requests import Request

from app.api.v1.module_common.file.service import FileService
from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_system.auth.oauth_service import (
    oauth_service_frontend_redirect_from_ticket,
    safe_frontend_redirect,
)
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.api.v1.module_system.user.model import UserModel, UserRolesModel
from app.config.setting import settings
from app.core.base_crud import CRUDBase
from app.core.base_model import ModelMixin
from app.core.client_ip import get_client_ip
from app.core.exceptions import CustomException
from app.plugin.module_ai.config import settings as ai_settings
from app.plugin.module_ai.config import validate_model_base_url
from app.plugin.module_ai.knowledge.crud import KnowledgeBaseCRUD
from app.plugin.module_ai.knowledge.model import KnowledgeBaseModel
from app.plugin.module_ai.memory.crud import MemoryCRUD
from app.plugin.module_ai.memory.model import AiMemoryModel
from app.plugin.module_ai.memory.schema import MemoryUpdateSchema
from app.utils.upload_util import UploadUtil


class PermissionRecord(ModelMixin):
    """Small model used to exercise CRUD DML permission predicates."""

    __tablename__ = "test_permission_record"

    created_id: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deleted_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value: Mapped[str] = mapped_column(String(64), nullable=False)


def _request_with_client(client: str, forwarded_for: str | None = None) -> Request:
    """Build a minimal Starlette request with an optional forwarding chain."""
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode("ascii")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (client, 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def _auth_for(user_id: int) -> SimpleNamespace:
    """Create the minimal auth object consumed by data-scope filters."""
    return SimpleNamespace(
        db=None,
        user=SimpleNamespace(id=user_id, is_superuser=False, roles=[]),
        check_data_scope=True,
    )


@pytest.mark.parametrize(
    ("direct_peer", "forwarded_for", "trusted", "expected"),
    [
        ("198.51.100.10", "203.0.113.10", [], "198.51.100.10"),
        ("10.0.0.1", "203.0.113.10, 10.0.0.2", ["10.0.0.0/8"], "203.0.113.10"),
        ("10.0.0.1", "not-an-ip", ["10.0.0.0/8"], "10.0.0.1"),
    ],
)
def test_client_ip_only_trusts_forwarding_headers_from_configured_proxies(
    monkeypatch,
    direct_peer: str,
    forwarded_for: str,
    trusted: list[str],
    expected: str,
) -> None:
    """A client must not spoof its login IP through an untrusted header."""
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", trusted)

    request = _request_with_client(direct_peer, forwarded_for)

    assert get_client_ip(request) == expected


@pytest.mark.asyncio
async def test_rate_limit_identifier_uses_trusted_client_ip(monkeypatch) -> None:
    """Rate limiter keys must only trust client IP headers from configured proxies."""
    from app.core.http_limit import rate_limit_identifier

    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["10.0.0.0/8"])
    trusted_request = _request_with_client("10.0.0.1", "203.0.113.10")
    assert await rate_limit_identifier(trusted_request) == "203.0.113.10:/"

    untrusted_request = _request_with_client("198.51.100.10", "203.0.113.10")
    assert await rate_limit_identifier(untrusted_request) == "198.51.100.10:/"


def test_oauth_frontend_redirect_is_allowlisted_and_ticket_only() -> None:
    """OAuth callbacks must not redirect to arbitrary origins or carry JWTs."""
    fallback = settings.OAUTH_FRONTEND_FALLBACK
    assert safe_frontend_redirect("https://evil.example/login") == fallback

    allowed = "http://127.0.0.1:5173/login?from=oauth"
    assert safe_frontend_redirect(allowed) == allowed

    callback = oauth_service_frontend_redirect_from_ticket(allowed, "opaque-ticket")
    assert "opaque-ticket" in callback
    assert "access_token" not in callback
    assert "refresh_token" not in callback


def test_model_provider_url_rejects_local_network_targets(monkeypatch) -> None:
    """User-configured model endpoints must not target local or private hosts."""
    import socket

    monkeypatch.setattr(ai_settings, "MODEL_ALLOWED_HOSTS", [])
    monkeypatch.setattr(
        "app.plugin.module_ai.config.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )

    with pytest.raises(ValueError):
        validate_model_base_url("http://127.0.0.1:8000/v1")
    with pytest.raises(ValueError):
        validate_model_base_url("http://169.254.169.254/latest/meta-data")
    assert validate_model_base_url("https://api.example.com/v1") == "https://api.example.com/v1"


def test_model_provider_url_rejects_private_dns_resolution(monkeypatch) -> None:
    """A public-looking hostname must not resolve to a private address."""
    import socket

    monkeypatch.setattr(ai_settings, "MODEL_ALLOWED_HOSTS", [])
    monkeypatch.setattr(
        "app.plugin.module_ai.config.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))],
    )

    with pytest.raises(ValueError, match="解析"):
        validate_model_base_url("https://model.example/v1")


@pytest.mark.asyncio
async def test_download_service_rejects_paths_outside_private_upload_root(tmp_path, monkeypatch) -> None:
    """The download endpoint must enforce containment after path resolution."""
    upload_root = tmp_path / "upload"
    upload_root.mkdir()
    safe_file = upload_root / "safe.txt"
    safe_file.write_text("safe", encoding="utf-8")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside", encoding="utf-8")
    monkeypatch.setattr(settings, "UPLOAD_FILE_PATH", upload_root)

    result = await FileService.download_service(str(safe_file))
    assert result.file_path == str(safe_file.resolve())

    with pytest.raises(CustomException):
        await FileService.download_service(str(upload_root / ".." / outside_file.name))


@pytest.mark.asyncio
async def test_upload_stream_enforces_size_limit_without_buffering_the_whole_file(tmp_path, monkeypatch) -> None:
    """Streaming upload validation must reject oversized bodies and leave no file behind."""
    monkeypatch.setattr(settings, "UPLOAD_FILE_PATH", tmp_path / "upload")
    monkeypatch.setattr(settings, "MAX_FILE_SIZE", 3)
    upload = UploadFile(file=io.BytesIO(b"1234"), filename="avatar.png")

    with pytest.raises(CustomException, match="文件大小超过限制"):
        await UploadUtil.upload_file(upload, "https://example.test")

    assert not (tmp_path / "upload").exists()


@pytest.mark.parametrize("entrypoint", ["stream", "upload", "knowledge"])
@pytest.mark.parametrize("symlink", [False, True])
async def test_upload_collision_preserves_existing_destination(tmp_path, monkeypatch, entrypoint, symlink):
    from app.plugin.module_ai.knowledge import document_lifecycle as knowledge_service

    filename = "kb-1-existing.txt" if entrypoint == "knowledge" else "existing.txt"
    destination = (tmp_path / "resource" if entrypoint == "upload" else tmp_path) / filename
    destination.parent.mkdir(exist_ok=True)
    original = tmp_path / "original.txt"
    if symlink:
        original.write_bytes(b"existing content")
        destination.symlink_to(original)
    else:
        destination.write_bytes(b"existing content")
    upload = UploadFile(file=io.BytesIO(b"new content"), filename="new.txt")
    monkeypatch.setattr(settings, "UPLOAD_FILE_PATH", tmp_path)
    monkeypatch.setattr(settings, "ALLOWED_EXTENSIONS", [*settings.ALLOWED_EXTENSIONS, ".txt"])
    monkeypatch.setattr(UploadUtil, "generate_safe_filename", lambda *_args: destination.name)
    monkeypatch.setattr(knowledge_service, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(knowledge_service.uuid, "uuid4", lambda: SimpleNamespace(hex="existing"))

    with pytest.raises(CustomException, match="文件保存失败"):
        if entrypoint == "stream":
            await UploadUtil.save_upload_stream(upload, destination)
        elif entrypoint == "upload":
            await UploadUtil.upload_file(upload, "https://example.test", upload_type="resource")
        else:
            await knowledge_service.KnowledgeDocumentLifecycle(SimpleNamespace())._save_upload_file(upload, upload_owner="kb-1")

    assert destination.is_symlink() == symlink
    assert destination.read_bytes() == b"existing content"
    if symlink:
        assert original.read_bytes() == b"existing content"


@pytest.mark.parametrize("failure", ["size", "read", "write"])
async def test_upload_stream_removes_new_destination_on_failure(tmp_path, monkeypatch, failure):
    from app.utils import upload_util

    destination = tmp_path / "new.txt"
    upload = UploadFile(file=io.BytesIO(b"new content"), filename="new.txt")
    if failure == "size":
        monkeypatch.setattr(settings, "MAX_FILE_SIZE", 1)
    elif failure == "read":
        monkeypatch.setattr(upload, "read", AsyncMock(side_effect=[b"partial", OSError("read failed")]))
    else:
        original_wrap = upload_util.aiofiles.threadpool.wrap

        def failing_write(*args, **kwargs):
            target = original_wrap(*args, **kwargs)
            monkeypatch.setattr(target, "write", AsyncMock(side_effect=OSError("write failed")))
            return target

        monkeypatch.setattr(upload_util.aiofiles.threadpool, "wrap", failing_write)

    with pytest.raises(CustomException):
        await UploadUtil.save_upload_stream(upload, destination)

    assert not destination.exists()


async def test_cancelled_upload_removes_partial_destination_and_propagates(tmp_path, monkeypatch):
    destination = tmp_path / "new.txt"
    upload = UploadFile(file=io.BytesIO(b"partial"), filename="new.txt")
    waiting = asyncio.Event()
    original_read = upload.read

    async def read(size):
        if upload.file.tell() == 0:
            return await original_read(size)
        waiting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(upload, "read", read)
    task = asyncio.create_task(UploadUtil.save_upload_stream(upload, destination))
    await asyncio.wait_for(waiting.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not destination.exists()


@pytest.mark.asyncio
async def test_upload_urls_and_preview_policies(tmp_path, monkeypatch) -> None:
    """Uploaded public images and private files must use separate preview policies."""
    upload_root = tmp_path / "upload"
    monkeypatch.setattr(settings, "UPLOAD_FILE_PATH", upload_root)
    png = b"\x89PNG\r\n\x1a\nminimal-test-image"

    public_upload = UploadFile(file=io.BytesIO(png), filename="avatar.png")
    _, public_path, public_url = await UploadUtil.upload_file(
        public_upload,
        "https://example.test/api/v1/",
        upload_type="avatar",
    )
    public_relative = public_path.resolve().relative_to(upload_root.resolve()).as_posix()
    assert public_url == f"https://example.test/api/v1/common/file/public-upload/{public_relative}"
    resolved_public, public_media_type = await FileService.preview_service(
        public_relative,
        public_images_only=True,
    )
    assert resolved_public == public_path.resolve()
    assert public_media_type == "image/png"

    private_upload = UploadFile(file=io.BytesIO(png), filename="document.png")
    _, private_path, private_url = await UploadUtil.upload_file(
        private_upload,
        "https://example.test/api/v1/",
        upload_type="file",
    )
    private_relative = private_path.resolve().relative_to(upload_root.resolve()).as_posix()
    assert private_url == f"https://example.test/api/v1/common/file/private-upload/{private_relative}"
    resolved_private, _ = await FileService.preview_service(private_relative)
    assert resolved_private == private_path.resolve()
    with pytest.raises(CustomException):
        await FileService.preview_service(private_relative, public_images_only=True)


def test_file_preview_routes_keep_private_files_authenticated(
    test_client: TestClient,
    tmp_path,
    monkeypatch,
) -> None:
    """Public image previews are anonymous while private previews require permission."""
    upload_root = tmp_path / "upload"
    public_file = upload_root / "avatar" / "2026" / "public.png"
    private_file = upload_root / "file" / "2026" / "private.png"
    public_file.parent.mkdir(parents=True)
    private_file.parent.mkdir(parents=True)
    content = b"\x89PNG\r\n\x1a\nroute-test"
    public_file.write_bytes(content)
    private_file.write_bytes(content)
    monkeypatch.setattr(settings, "UPLOAD_FILE_PATH", upload_root)

    public_response = test_client.get("/common/file/public-upload/avatar/2026/public.png")
    assert public_response.status_code == 200
    assert public_response.content == content

    anonymous_private = test_client.get("/common/file/private-upload/file/2026/private.png")
    assert anonymous_private.status_code == 401
    login_response = test_client.post(
        "/system/auth/login",
        data={"username": "admin", "password": "admin123"},
    )
    assert login_response.status_code == 200
    auth_headers = {"Authorization": f"Bearer {login_response.json()['data']['access_token']}"}
    authenticated_private = test_client.get(
        "/common/file/private-upload/file/2026/private.png",
        headers=auth_headers,
    )
    assert authenticated_private.status_code == 200
    assert authenticated_private.content == content

    private_via_public_route = test_client.get("/common/file/public-upload/file/2026/private.png")
    assert private_via_public_route.status_code == 400


def test_captcha_endpoint_returns_a_login_challenge(test_client: TestClient) -> None:
    """The login page can obtain a challenge even when adaptive mode is off initially."""
    response = test_client.get("/system/auth/captcha/get")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["key"]
    assert body["data"]["img_base"].startswith("data:image/png;base64,")
    assert body["data"]["enable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("offset", "search", "total", "values", "query_count"),
    [
        (0, {"value": "absent"}, 0, [], 1),
        (2, {}, 2, [], 1),
        (0, {}, 2, ["first", "second"], 2),
        (1, {}, 2, ["second"], 2),
    ],
)
async def test_crud_page_skips_empty_data_queries_and_preserves_scope(tmp_path, offset, search, total, values, query_count):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'page.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(PermissionRecord.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add_all([
                PermissionRecord(created_id=1, value="first"),
                PermissionRecord(created_id=2, value="foreign"),
                PermissionRecord(created_id=1, value="deleted", is_deleted=True),
                PermissionRecord(created_id=1, value="second"),
            ])
            await db.commit()
            auth = _auth_for(1)
            auth.db = db
            statements = []

            def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
                if statement.lstrip().upper().startswith("SELECT"):
                    statements.append(statement)

            event.listen(engine.sync_engine, "before_cursor_execute", capture)
            page = await CRUDBase(PermissionRecord, auth=auth).page(
                offset=offset, limit=2, order_by=[{"id": "asc"}], search=search, preload=[]
            )
            assert page.total == total
            assert page.page_no == offset // 2 + 1
            assert page.page_size == 2
            assert page.has_next is False
            assert [item.value for item in page.items] == values
            assert len(statements) == query_count, statements

            with pytest.raises(CustomException, match="分页查询失败"):
                await CRUDBase(PermissionRecord, auth=auth).page(
                    offset=0, limit=2, order_by=[{"missing": "asc"}], search={"value": "absent"}, preload=[]
                )
            with pytest.raises(CompileError):
                await CRUDBase(PermissionRecord, auth=auth).page(
                    offset=offset, limit=2, order_by=[{"__tablename__": "asc"}], search=search, preload=[]
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_crud_dml_operations_apply_data_scope_to_every_row(tmp_path) -> None:
    """Bulk update/delete/restore/clear must never affect another user's row."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'permission.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(PermissionRecord.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        owned = PermissionRecord(created_id=1, value="owned")
        foreign = PermissionRecord(created_id=2, value="foreign")
        db.add_all([owned, foreign])
        await db.commit()

        auth = _auth_for(1)
        auth.db = db
        crud = CRUDBase(model=PermissionRecord, auth=auth)

        await crud.set([owned.id, foreign.id], value="changed")
        await db.commit()
        await db.refresh(owned)
        await db.refresh(foreign)
        assert owned.value == "changed"
        assert foreign.value == "foreign"

        await crud.delete([owned.id, foreign.id])
        await db.commit()
        await db.refresh(owned)
        await db.refresh(foreign)
        assert owned.is_deleted is True
        assert foreign.is_deleted is False

        await crud.restore([owned.id, foreign.id])
        await db.commit()
        await db.refresh(owned)
        await db.refresh(foreign)
        assert owned.is_deleted is False
        assert foreign.is_deleted is False

        await crud.clear()
        await db.commit()
        await db.refresh(owned)
        await db.refresh(foreign)
        assert owned.is_deleted is True
        assert foreign.is_deleted is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_memory_page_and_mutations_are_scoped_to_user_id(tmp_path) -> None:
    """Memory pagination and mutation lookups must isolate users by immutable ID."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(AiMemoryModel.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        db.add_all(
            [
                AiMemoryModel(user_id="1", key="one-a", value="a", priority=2),
                AiMemoryModel(user_id="1", key="one-b", value="b", priority=1),
                AiMemoryModel(user_id="1", key="one-c", value="c", priority=0),
                AiMemoryModel(user_id="2", key="two-a", value="x", priority=9),
            ]
        )
        await db.commit()

        auth_one = _auth_for(1)
        auth_one.db = db
        auth_two = _auth_for(2)
        auth_two.db = db
        user_one = MemoryCRUD(auth_one)
        user_two = MemoryCRUD(auth_two)

        page, total = await user_one.page_crud(offset=1, limit=1)
        assert total == 3
        assert len(page) == 1
        assert page[0].user_id == "1"

        foreign_id = (await user_two.list_crud())[0].id
        assert await user_one.update_crud(foreign_id, MemoryUpdateSchema(value="y")) is False
        assert await user_one.delete_crud([foreign_id]) is True
        foreign = await db.scalar(select(AiMemoryModel).where(AiMemoryModel.id == foreign_id))
        assert foreign is not None and foreign.is_deleted is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_knowledge_base_crud_rejects_cross_user_reads_and_deletes(tmp_path) -> None:
    """Knowledge-base CRUD must apply the same creator scope as ordinary CRUD."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}")
    async with engine.begin() as connection:
        for table in (
            MenuModel.__table__,
            UserModel.__table__,
            RoleModel.__table__,
            RoleMenusModel.__table__,
            UserRolesModel.__table__,
        ):
            await connection.run_sync(table.create)
        await connection.run_sync(KnowledgeBaseModel.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        db.add_all(
            [
                UserModel(username="owner", password="hash", name="Owner"),
                UserModel(username="other", password="hash", name="Other"),
            ]
        )
        await db.flush()
        first = KnowledgeBaseModel(name="owned", created_id=1)
        second = KnowledgeBaseModel(name="foreign", created_id=2)
        db.add_all([first, second])
        await db.commit()

        auth = _auth_for(1)
        auth.db = db
        crud = KnowledgeBaseCRUD(auth)

        assert await crud.get_by_id(second.id) is None
        page = await crud.page(offset=0, limit=20, order_by=[{"id": "asc"}], search={}, preload=[])
        assert page.total == 1
        assert [item.id for item in page.items] == [first.id]

        await crud.delete([second.id])
        await db.commit()
        foreign = await db.scalar(select(KnowledgeBaseModel).where(KnowledgeBaseModel.id == second.id))
        assert foreign is not None and foreign.is_deleted is False

    await engine.dispose()
