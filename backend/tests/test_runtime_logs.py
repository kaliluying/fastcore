"""独立运行：PYTHONPATH=. .venv/bin/pytest --noconftest tests/test_runtime_logs.py。

避免公共 conftest 初始化应用；首次导入 logger 前先替换 LOG_DIR，所有日志
均在临时目录，审计 logger 单独 mock，不访问或写入现有运行日志。
"""

import gzip
import importlib
import json
import os
import stat
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

NOW = datetime(2026, 10, 3, 12, 0, 0)
ADMIN = SimpleNamespace(user=SimpleNamespace(id=7, is_superuser=True))


@pytest.fixture
def logs(tmp_path, monkeypatch):
    from app.config import path_conf

    directory = tmp_path.resolve()
    monkeypatch.setattr(path_conf, "LOG_DIR", directory)
    module = importlib.import_module("app.plugin.module_ai.chat.runtime_logs")
    monkeypatch.setattr(module, "LOG_DIR", directory)
    monkeypatch.setattr(module, "logger", SimpleNamespace(info=Mock()))

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(module, "datetime", FixedDateTime)
    return module


def record(message, *, timestamp=None, level="ERROR", source="app.worker:run:42", cid=""):
    timestamp = timestamp or NOW - timedelta(minutes=1)
    context = f" | cid={cid}" if cid else ""
    return f"{timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} | {level:<8} | {source} - {message}{context}\n"


def write_records(logs, messages):
    data = ("".join(messages) + record("next record boundary", level="INFO", timestamp=NOW + timedelta(minutes=1))).encode()
    (logs.LOG_DIR / "fastcore.log").write_bytes(data)
    return data


async def query(logs, **kwargs):
    return await logs.query_runtime_logs(ADMIN, logs.RuntimeLogQuerySchema(**kwargs))


@pytest.mark.parametrize("quote", ['"', "'"])
@pytest.mark.parametrize("tail", ["", "\\", "\\\n"])
@pytest.mark.parametrize("key", ["password", "cookie", "cookies", "set-cookie", "authorization", "proxy-authorization"])
def test_redaction_covers_unclosed_multiline_quoted_secrets(logs, quote, tail, key):
    text = f"公开说明\n{key}={quote}synthetic-line-one\nsynthetic-line-two{tail}"
    result = logs.redact_log_text(text)
    assert "synthetic-" not in result
    assert result == f"公开说明\n{key}=[REDACTED]"
    if tail != "\\":
        assert logs.redact_log_text(text + quote + "\n可见后文").endswith("\n可见后文")


@pytest.mark.asyncio
async def test_complete_evidence_includes_info_casefold_and_long_exception(logs):
    trace = "Traceback\n" + "  worker frame\n" * 1000 + "ValueError: ROOT-CAUSE-END"
    write_records(logs, [record(f"TimeoutError-{index}", level="INFO") for index in range(70)] + [record("TimeoutError\n" + trace)])
    result = await query(logs, keyword="timeouterror")
    assert result["matched_count"] == len(result["entries"]) == 71
    assert any(entry["level"] == "INFO" for entry in result["entries"])
    assert any(entry["message"].endswith("ROOT-CAUSE-END") for entry in result["entries"])
    assert len(json.dumps(result["entries"])) > 12000
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_full_active_file_and_rotated_archives_are_queried(logs):
    old_time = NOW - timedelta(days=2)
    active = record("ACTIVE-BEGIN") + record("large trace\n" + " frame\n" * 320000) + record("ACTIVE-END")
    (logs.LOG_DIR / "fastcore.log").write_text(active)
    archive = record("ARCHIVE-ROOT token=archive-secret", timestamp=old_time)
    with gzip.open(logs.LOG_DIR / "fastcore.2026-10-01_12-00-00_123456.log.gz", "wt") as handle:
        handle.write(archive)
    (logs.LOG_DIR / "fastcore.2026-10-02.log").write_text(record("PLAIN-ARCHIVE", timestamp=old_time))
    (logs.LOG_DIR / "unrelated.log").write_text(record("MUST-NOT-READ"))
    result = await query(logs, minutes=3 * 1440)
    messages = "\n".join(entry["message"] for entry in result["entries"])
    for expected in ("ACTIVE-BEGIN", "ACTIVE-END", "ARCHIVE-ROOT", "PLAIN-ARCHIVE"):
        assert expected in messages
    assert "archive-secret" not in messages and "MUST-NOT-READ" not in messages
    assert result["scanned_bytes"] > 2 * 1024 * 1024
    assert len(result["scanned_files"]) == 3
    assert result["truncated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [
    "fastcore.2026-10-01_12-00-00_123456.2.log.gz",
    "fastcore.2026-10-01_12-00-00_123456.2.2026-10-02_12-00-00_654321.2.log.gz",
    "fastcore.log.gz",
])
async def test_loguru_archive_collision_names_are_included(logs, name):
    with gzip.open(logs.LOG_DIR / name, "wt") as archive:
        archive.write(record("COLLISION-ARCHIVE"))
    result = await query(logs)
    assert result["matched_count"] == 1
    assert result["entries"][0]["file"] == name


# gzip 头部内嵌 mtime，直接调用 gzip.compress 会让参数化 ID 随时间变化，
# xdist 并行收集时各 worker 用例列表不一致导致报错；改用 mtime=0 的固定字节。
_CORRUPT_GZIP_BYTES = bytes.fromhex("1f8b0800000000000002ff2b4a4dce2f4a010000919f349b")


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", [b"not gzip", _CORRUPT_GZIP_BYTES])
async def test_corrupt_archive_fails_without_exposing_content(logs, contents):
    (logs.LOG_DIR / "fastcore.2026-10-02.log.gz").write_bytes(contents)
    with pytest.raises(logs.CustomException) as caught:
        await query(logs)
    assert caught.value.status_code == 503
    assert caught.value.msg == "运行日志暂时无法读取"


@pytest.mark.asyncio
async def test_archive_symlink_is_rejected(logs):
    actual = logs.LOG_DIR / "private.txt"
    actual.write_text(record("DO-NOT-READ"))
    (logs.LOG_DIR / "fastcore.2026-10-02.log").symlink_to(actual)
    with pytest.raises(logs.CustomException) as caught:
        await query(logs)
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_cancellation_stops_background_scan(logs, monkeypatch):
    started = threading.Event()
    stopped = threading.Event()

    def wait_for_cancel(query, start_time, end_time, cancelled):
        started.set()
        assert cancelled.wait(2)
        stopped.set()

    monkeypatch.setattr(logs, "_query_snapshot", wait_for_cancel)
    task = logs.asyncio.create_task(query(logs))
    assert await logs.asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(logs.asyncio.CancelledError):
        await task
    assert await logs.asyncio.to_thread(stopped.wait, 2)
    logs.logger.info.assert_not_called()


def test_query_defaults_and_independent_levels(logs):
    first = logs.RuntimeLogQuerySchema()
    second = logs.RuntimeLogQuerySchema()
    assert first.model_dump() == {"minutes": 60, "levels": ["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"], "keyword": "", "correlation_id": ""}
    first.levels.clear()
    assert second.levels == ["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]
    assert logs.RuntimeLogQuerySchema(minutes=5, correlation_id="A_b-9").minutes == 5
    assert logs.RuntimeLogQuerySchema(minutes=1440, levels=["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]).minutes == 1440
    assert logs.RuntimeLogQuerySchema(minutes=logs.MAX_QUERY_MINUTES).minutes == logs.MAX_QUERY_MINUTES


@pytest.mark.parametrize(
    "values",
    [
        {"minutes": 0},
        {"minutes": -1},
        {"minutes": 7 * 24 * 60 + 1},
        {"levels": []},
        {"levels": ["error"]},
        {"correlation_id": "a" * 65},
        {"correlation_id": "abc.xyz"},
        {"correlation_id": "abc\n"},
        {"path": "/tmp/other.log"},
        {"sql": "SELECT 1"},
        {"commands": ["pwd"]},
    ],
)
def test_query_validation_forbids_other_inputs(logs, values):
    with pytest.raises(ValidationError):
        logs.RuntimeLogQuerySchema(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "auth",
    [None, SimpleNamespace(), SimpleNamespace(user=None), SimpleNamespace(user=SimpleNamespace()), *[SimpleNamespace(user=SimpleNamespace(is_superuser=value)) for value in (False, None, 1, "true")]],
)
async def test_permission_strictly_precedes_read_and_offload(logs, monkeypatch, auth):
    read = Mock(side_effect=AssertionError("must not read"))
    offload = Mock(side_effect=AssertionError("must not offload"))
    monkeypatch.setattr(logs, "_read_snapshot", read)
    monkeypatch.setattr(logs.asyncio, "to_thread", offload)
    with pytest.raises(logs.CustomException) as caught:
        await logs.query_runtime_logs(auth, logs.RuntimeLogQuerySchema())
    assert caught.value.status_code == 403
    read.assert_not_called()
    offload.assert_not_called()
    logs.logger.info.assert_not_called()


@pytest.mark.asyncio
async def test_missing_file_or_directory_and_unrelated_files_are_not_read(logs, monkeypatch):
    (logs.LOG_DIR / "unrelated.log").write_text(record("archive secret"))
    for directory in (logs.LOG_DIR, logs.LOG_DIR / "missing"):
        monkeypatch.setattr(logs, "LOG_DIR", directory)
        result = await query(logs)
        assert result["entries"] == []
        assert result["scanned_bytes"] == result["matched_count"] == 0
        assert result["truncated"] is False
        assert "不存在" in result["notice"] and "非全局" in result["notice"]
        assert "archive secret" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["file", "directory", "dangling"])
async def test_symlinks_are_rejected_without_read(logs, monkeypatch, target):
    directory = logs.LOG_DIR
    actual = directory / "real.log"
    actual.write_text(record("secret"))
    if target == "directory":
        link = directory / "linked-directory"
        link.symlink_to(directory, target_is_directory=True)
        monkeypatch.setattr(logs, "LOG_DIR", link)
    else:
        (directory / "fastcore.log").symlink_to(actual if target == "file" else directory / "absent")
    read = Mock(side_effect=AssertionError("must not read"))
    monkeypatch.setattr(logs.os, "read", read)
    with pytest.raises(logs.CustomException) as caught:
        await query(logs)
    assert caught.value.status_code == 503
    read.assert_not_called()


@pytest.mark.asyncio
async def test_fifo_rejected_and_secure_open_flags(logs, monkeypatch):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO unavailable")
    path = logs.LOG_DIR / "fastcore.log"
    os.mkfifo(path)
    with pytest.raises(logs.CustomException):
        await query(logs)
    path.unlink()
    write_records(logs, [record("regular")])
    original_open = os.open
    opened = []

    def capture_open(path, flags, *args, **kwargs):
        opened.append((path, flags, kwargs))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(logs.os, "open", capture_open)
    monkeypatch.setattr(logs.os, "supports_dir_fd", {*os.supports_dir_fd, capture_open})
    await query(logs)
    file_open = next(item for item in opened if item[0] == "fastcore.log")
    assert file_open[1] & os.O_NOFOLLOW
    assert file_open[1] & os.O_NONBLOCK
    assert file_open[1] & os.O_ACCMODE == os.O_RDONLY
    assert isinstance(file_open[2]["dir_fd"], int)


@pytest.mark.asyncio
async def test_no_nofollow_support_fails_closed(logs, monkeypatch):
    monkeypatch.delattr(logs.os, "O_NOFOLLOW")
    with pytest.raises(logs.CustomException) as caught:
        await query(logs)
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_time_level_literal_keyword_and_cid_filters(logs):
    write_records(
        logs,
        [
            record("outside", timestamp=NOW - timedelta(minutes=60, milliseconds=1)),
            record("edge [literal].*", timestamp=NOW - timedelta(minutes=60), level="WARNING", cid="abcdefgh"),
            record("wrong level [literal].*", level="INFO", cid="abcdefgh"),
            record("wrong cid [literal].*", cid="other-id"),
            record("regex not a match literal", cid="abcdefgh"),
            record("password=unique_secret"),
            record("now [literal].*", timestamp=NOW, level="CRITICAL", cid="abcdefgh"),
            record("future [literal].*", timestamp=NOW + timedelta(milliseconds=1), cid="abcdefgh"),
        ],
    )
    result = await query(logs, levels=["WARNING", "ERROR", "CRITICAL"], keyword="[literal].*", correlation_id="abcdefgh-full-request-id")
    assert result["matched_count"] == 2
    assert [entry["level"] for entry in result["entries"]] == ["CRITICAL", "WARNING"]
    assert result["start_time"] == (NOW - timedelta(minutes=60)).isoformat()
    assert result["end_time"] == NOW.isoformat()
    assert "前 8 位" in result["notice"] and "碰撞" in result["notice"]
    assert (await query(logs, keyword="unique_secret"))["matched_count"] == 0
    assert (await query(logs, levels=["INFO"]))["matched_count"] == 1
    assert (await query(logs, keyword="[LITERAL].*"))["matched_count"] == 4


@pytest.mark.asyncio
async def test_full_cid_matches_exactly_and_unsafe_cid_is_not_exposed(logs):
    write_records(logs, [record("one", cid="abcdefgh-full-one"), record("two", cid="abcdefgh-full-two"), record("phone cid", cid="13800138000")])
    result = await query(logs, correlation_id="abcdefgh-full-one")
    assert result["matched_count"] == 1
    assert result["entries"][0]["correlation_id"] == "abcdefgh-full-one"
    result = await query(logs)
    assert result["entries"][0]["correlation_id"] == ""
    assert "13800138000" not in json.dumps(result)


@pytest.mark.asyncio
async def test_multiline_utf8_and_incomplete_edges(logs, monkeypatch):
    prefix = "中文首条" * 40
    content = (record(prefix) + record("中文异常") + 'Traceback (most recent call last):\n  File "worker.py", line 12\nValueError: 操作失败\n' + record("incomplete")[:-3]).encode()
    (logs.LOG_DIR / "fastcore.log").write_bytes(content)
    result = await query(logs)
    assert result["matched_count"] == 2
    assert prefix in json.dumps(result, ensure_ascii=False)
    assert "中文异常" in json.dumps(result, ensure_ascii=False)
    assert result["truncated"] is True
    content = content[: -len(record("incomplete")[:-3].encode())] + record("boundary", level="INFO").encode()
    (logs.LOG_DIR / "fastcore.log").write_bytes(content)
    result = await query(logs, levels=["ERROR"])
    assert result["matched_count"] == 2
    message = "\n".join(entry["message"] for entry in result["entries"])
    assert "中文异常" in message and "ValueError: 操作失败" in message and "Traceback" in message
    assert "�" not in message and prefix in message
    assert result["truncated"] is False


@pytest.mark.parametrize(
    "text,secrets",
    [
        ("Authorization: Bearer auth_secret", ["auth_secret"]),
        ('{"authorization": "Basic YmFzaWNfc2VjcmV0"}', ["YmFzaWNfc2VjcmV0"]),
        ("Cookie: session=COOKIE_SECRET; csrf=CSRF_SECRET", ["COOKIE_SECRET", "CSRF_SECRET"]),
        ("Set-Cookie: sid=SET_COOKIE_SECRET; HttpOnly", ["SET_COOKIE_SECRET"]),
        ('{"cookies": {"session": "JSON_COOKIE_SECRET"}}', ["JSON_COOKIE_SECRET"]),
        ('{"session": "SESSION_SECRET", "nested": {"api_key": "NESTED_SECRET"}}', ["SESSION_SECRET", "NESTED_SECRET"]),
        ('{"password": "pword secret", "api_key": "API_SECRET", "refresh_token": "TOKEN_SECRET"}', ["pword secret", "API_SECRET", "TOKEN_SECRET"]),
        ("passwd='spaced secret' credential=CRED_SECRET access-key=ACCESS_SECRET", ["spaced secret", "CRED_SECRET", "ACCESS_SECRET"]),
        ("postgresql://account:URL_SECRET@database.example/db", ["account", "URL_SECRET"]),
        ("https://api.example/path?api_key=QUERY_SECRET&x=1", ["QUERY_SECRET"]),
        ("Bearer bare_secret Basic YW5vdGhlcl9zZWNyZXQ=", ["bare_secret", "YW5vdGhlcl9zZWNyZXQ"]),
        ("JWT eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature", ["eyJhbGci", "eyJzdWIi", "signature"]),
        ("-----BEGIN PRIVATE KEY-----\nPRIVATE_SECRET\n-----END PRIVATE KEY-----", ["PRIVATE_SECRET"]),
        ("-----BEGIN RSA PRIVATE KEY-----\nUNCLOSED_SECRET", ["UNCLOSED_SECRET"]),
        (
            "test.person@example.com +86 13800138000 +1 (212) 555-0123 10.23.45.67 2001:db8::1 [::1] fe80::1%en0 [::]",
            ["test.person", "13800138000", "555-0123", "10.23.45.67", "2001:db8::1", "::1", "fe80::1", "::"],
        ),
        ("IPv6 2001:db8::1. IPv4 10.23.45.67:8080", ["2001:db8::1", "10.23.45.67"]),
        ("│   └ 'ARBITRARY_LOCAL_SECRET'\n└ {'unusual': 'OTHER_LOCAL_SECRET'}", ["ARBITRARY_LOCAL_SECRET", "OTHER_LOCAL_SECRET"]),
        ("sk-proj-STANDALONE_SECRET ghp_GITHUB_SECRET xoxb-SLACK-SECRET", ["STANDALONE_SECRET", "GITHUB_SECRET", "SLACK-SECRET"]),
    ],
)
def test_exported_redaction(logs, text, secrets):
    redacted = logs.redact_log_text(text)
    for secret in secrets:
        assert secret not in redacted
    assert "[REDACTED]" in redacted


def test_large_unkeyed_text_redaction_is_bounded(logs):
    text = "token" * 20000 + " " + "segment." * 15000
    assert logs.redact_log_text(text) == text


@pytest.mark.asyncio
async def test_source_and_full_record_redacted_before_keyword_matching(logs):
    secret = "BOUNDARY_SECRET" * 300
    message = "visible " + "a" * 1800 + f' password="{secret}"\n│   └ ARBITRARY_LOCAL_SECRET\n' + "suffix-marker"
    write_records(logs, [record(message, source="person@example.com:run:42")])
    result = await query(logs, keyword="suffix-marker")
    assert result["matched_count"] == 1
    serialized = json.dumps(result, ensure_ascii=False)
    for secret_fragment in ("BOUNDARY_SECRET", "ARBITRARY_LOCAL_SECRET", "person@example.com"):
        assert secret_fragment not in serialized
    assert "[REDACTED]" in result["entries"][0]["message"]
    assert result["entries"][0]["message"].endswith("suffix-marker")
    assert result["truncated"] is False
    assert (await query(logs, keyword="BOUNDARY_SECRET"))["matched_count"] == 0


@pytest.mark.asyncio
async def test_all_matching_records_have_stable_ids_without_character_clipping(logs):
    write_records(logs, [record(f"message-{index}", timestamp=NOW - timedelta(seconds=100 - index)) for index in range(70)])
    result = await query(logs)
    assert result["matched_count"] == 70
    assert len(result["entries"]) == 70
    assert "message-69" in result["entries"][0]["message"]
    assert "message-0" in result["entries"][-1]["message"]
    ids = [entry["id"] for entry in result["entries"]]
    assert len(set(ids)) == 70
    assert ids == [entry["id"] for entry in (await query(logs))["entries"]]
    assert result["truncated"] is False
    write_records(logs, [record("x\\\n" * 1700, timestamp=NOW - timedelta(seconds=index + 1)) for index in range(60)])
    result = await query(logs)
    assert result["matched_count"] == 60
    assert len(result["entries"]) == 60
    assert all(len(entry["message"]) > 2000 for entry in result["entries"])
    assert len(json.dumps(result["entries"])) > 12000
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_complete_file_is_read_in_chunks_on_background_thread(logs, monkeypatch):
    content = b"x" * (3 * 1024 * 1024) + b"\n" + record("tail record").encode() + record("boundary", level="INFO").encode()
    (logs.LOG_DIR / "fastcore.log").write_bytes(content)
    original_read = os.read
    calls = []
    main_thread = threading.get_ident()

    def bounded_read(descriptor, size):
        calls.append((size, threading.get_ident()))
        return original_read(descriptor, size)

    monkeypatch.setattr(logs.os, "read", bounded_read)
    result = await query(logs, levels=["ERROR"])
    assert result["scanned_bytes"] == len(content) == sum(size for size, _ in calls)
    assert all(size <= 64 * 1024 and thread != main_thread for size, thread in calls)
    assert result["matched_count"] == 1
    assert "tail record" in result["entries"][0]["message"]
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_scan_budget_stops_file_bytes_records_and_matches(logs, monkeypatch):
    data = write_records(logs, [record(f"match-{index}") for index in range(4)])
    monkeypatch.setattr(logs, "MAX_MATCHED_ENTRIES", 2)
    result = await query(logs)
    assert result["matched_count"] == 2
    assert len(result["entries"]) == 2
    assert result["truncated"] is True

    monkeypatch.setattr(logs, "MAX_MATCHED_ENTRIES", 100)
    monkeypatch.setattr(logs, "MAX_SCAN_BYTES", len(data) - 1)
    result = await query(logs)
    assert result["scanned_bytes"] == 0
    assert result["scanned_files"] == []
    assert result["truncated"] is True

    monkeypatch.setattr(logs, "MAX_SCAN_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr(logs, "MAX_RECORD_BYTES", 128)
    write_records(logs, [record("small"), record("x" * 200), record("tail")])
    result = await query(logs)
    messages = "\n".join(entry["message"] for entry in result["entries"])
    assert "small" in messages and "tail" in messages and "x" * 200 not in messages
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_compressed_expansion_budget_is_bounded(logs, monkeypatch):
    monkeypatch.setattr(logs, "MAX_SCAN_BYTES", 128)
    with gzip.open(logs.LOG_DIR / "fastcore.2026-10-02.log.gz", "wb") as archive:
        archive.write(b"x" * 4096)
    result = await query(logs)
    assert result["scanned_files"] == ["fastcore.2026-10-02.log.gz"]
    assert result["truncated"] is True
    assert result["matched_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["append", "rotate", "truncate"])
async def test_snapshot_not_followed_or_reopened(logs, monkeypatch, mutation):
    data = write_records(logs, [record("original")])
    path = logs.LOG_DIR / "fastcore.log"
    original_read = os.read
    reads = 0

    def mutate_after_read(descriptor, size):
        nonlocal reads
        chunk = original_read(descriptor, size)
        reads += 1
        if mutation == "append":
            with path.open("ab") as handle:
                handle.write(record("appended secret").encode())
        elif mutation == "rotate":
            path.rename(logs.LOG_DIR / "archive.log")
            path.write_text(record("replacement secret"))
        else:
            path.write_bytes(b"")
        return chunk

    monkeypatch.setattr(logs.os, "read", mutate_after_read)
    if mutation == "truncate":
        with pytest.raises(logs.CustomException):
            await query(logs)
    else:
        result = await query(logs)
        assert result["scanned_bytes"] == len(data)
        assert result["matched_count"] == 1
        assert "original" in result["entries"][0]["message"]
        assert "secret" not in json.dumps(result)
    assert reads == 1


@pytest.mark.asyncio
async def test_identity_changed_before_open_rejected_without_read(logs, monkeypatch):
    write_records(logs, [record("original")])
    original_open = os.open

    def swapped_open(path, flags, *args, **kwargs):
        if path == "fastcore.log":
            actual = logs.LOG_DIR / path
            actual.rename(logs.LOG_DIR / "old.log")
            actual.write_text(record("different identity"))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(logs.os, "open", swapped_open)
    monkeypatch.setattr(logs.os, "supports_dir_fd", {*os.supports_dir_fd, swapped_open})
    read = Mock(side_effect=AssertionError("must not read"))
    monkeypatch.setattr(logs.os, "read", read)
    with pytest.raises(logs.CustomException):
        await query(logs)
    read.assert_not_called()


@pytest.mark.asyncio
async def test_fstat_must_be_regular(logs, monkeypatch):
    write_records(logs, [record("original")])
    original_fstat = os.fstat

    def invalid_fstat(descriptor):
        actual = original_fstat(descriptor)
        if stat.S_ISREG(actual.st_mode):
            return SimpleNamespace(st_mode=stat.S_IFIFO, st_dev=actual.st_dev, st_ino=actual.st_ino)
        return actual

    monkeypatch.setattr(logs.os, "fstat", invalid_fstat)
    with pytest.raises(logs.CustomException):
        await query(logs)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [PermissionError("/secret/path API_TOKEN"), OSError("credentials=secret"), FileNotFoundError("rotation race")])
async def test_read_errors_are_generic_and_do_not_audit_content(logs, monkeypatch, error):
    monkeypatch.setattr(logs, "_read_snapshot", Mock(side_effect=error))
    with pytest.raises(logs.CustomException) as caught:
        await query(logs, keyword="private-keyword")
    assert caught.value.status_code == 503
    assert caught.value.msg == "运行日志暂时无法读取"
    assert caught.value.data is None and caught.value.__suppress_context__
    logs.logger.info.assert_not_called()


@pytest.mark.asyncio
async def test_audit_records_only_admin_id_and_counts(logs):
    write_records(logs, [record("unique-user-content", cid="abcdefgh")])
    result = await query(logs, keyword="unique-user-content", correlation_id="abcdefgh")
    logs.logger.info.assert_called_once_with(
        "Runtime log query admin_id={} scanned_bytes={} matched_count={} returned_count={}",
        7,
        result["scanned_bytes"],
        1,
        1,
    )
    assert "unique-user-content" not in repr(logs.logger.info.call_args)
    assert "abcdefgh" not in repr(logs.logger.info.call_args)


@pytest.mark.asyncio
async def test_single_complete_active_file_record_is_returned(logs):
    data = record("LOG-ACCEPTANCE-42 retrieval failed token=fixture-secret", source="app.knowledge:index:42", cid="abcd1234").encode()
    (logs.LOG_DIR / "fastcore.log").write_bytes(data)
    result = await query(logs)
    assert result["scanned_bytes"] == len(data)
    assert result["matched_count"] == len(result["entries"]) == 1
    assert "LOG-ACCEPTANCE-42" in result["entries"][0]["message"]
    assert result["entries"][0]["correlation_id"] == "abcd1234"
    assert "fixture-secret" not in result["entries"][0]["message"]
    assert result["truncated"] is False
    (logs.LOG_DIR / "fastcore.log").write_bytes(data[:-1])
    result = await query(logs)
    assert result["matched_count"] == 0 and result["entries"] == [] and result["truncated"]


@pytest.mark.asyncio
async def test_multiline_message_cid_and_id_survive_append_and_archives(logs, monkeypatch):
    prefix = record("discard me " * 100)
    write_records(logs, [prefix, record("multiline\nsecond line", cid="abcdefgh")])
    first = await query(logs, correlation_id="abcdefgh-full")
    assert first["matched_count"] == 1
    assert "second line" in first["entries"][0]["message"]
    with (logs.LOG_DIR / "fastcore.log").open("ab") as handle:
        handle.write(record("next boundary", level="INFO").encode())
    assert first["entries"][0]["id"] == (await query(logs, correlation_id="abcdefgh-full"))["entries"][0]["id"]
    (logs.LOG_DIR / "fastcore.2026-10-02.log").write_text(record("other archive"))
    assert first["entries"][0]["id"] == (await query(logs, correlation_id="abcdefgh-full"))["entries"][0]["id"]


@pytest.mark.asyncio
async def test_short_reads_remain_bounded_and_eof_fails_closed(logs, monkeypatch):
    data = write_records(logs, [record("complete")])
    original_read = os.read

    def short_read(descriptor, size):
        return original_read(descriptor, min(size, 7))

    monkeypatch.setattr(logs.os, "read", short_read)
    result = await query(logs)
    assert result["scanned_bytes"] == len(data) and result["matched_count"] == 1
    monkeypatch.setattr(logs.os, "read", lambda descriptor, size: b"")
    with pytest.raises(logs.CustomException):
        await query(logs)
