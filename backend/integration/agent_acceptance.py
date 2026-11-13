"""Disposable MySQL/PostgreSQL, Redis, HTTP and Chrome diagnostic-agent acceptance.

Run from backend: .venv/bin/python integration/agent_acceptance.py
No repository environment files or existing database services are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from processes import run_isolated

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
MARKER = "AGENTORCHIDEVIDENCE"
PASSWORD = "isolated-agent-acceptance"
MYSQL = Path("/opt/homebrew/opt/mysql@8.4/bin")
POSTGRES = Path("/opt/homebrew/opt/postgresql@18/bin")


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    return free_port() if port in {3306, 5432, 6379} else port


def validate_root(root: Path) -> None:
    if not root.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()) or not root.name.startswith("fastcore-integration-"):
        raise ValueError("Only a disposable system temporary directory is allowed")


def clean_environment() -> dict[str, str]:
    # Do not inherit database, provider, proxy or application settings from the caller.
    # PostgreSQL 18 aborts when LC_ALL/LANG are empty ("postmaster became multithreaded").
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT") if key in os.environ and os.environ[key]}
    environment.setdefault("LC_ALL", "C")
    environment.setdefault("LANG", "C")
    return environment


def wait_until(probe, *, timeout=60, description="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = probe()
        if result:
            return result
        time.sleep(0.15)
    raise AssertionError(f"Timed out waiting for {description}")


class Processes:
    def __init__(self, root: Path):
        self.root = root
        self.children = []
        self.sockets = []

    def start(self, name, command, environment):
        with (self.root / f"{name}.log").open("w") as log:
            child = subprocess.Popen(command, cwd=BACKEND, env=environment, stdout=log, stderr=log, start_new_session=True)
        self.children.append(child)
        return child

    def stop(self, child, *, kill=False):
        if child.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL if kill else signal.SIGTERM)
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
                if not kill:
                    raise AssertionError(f"Process {child.pid} did not stop gracefully")

    def close(self):
        for child in reversed(self.children):
            try:
                self.stop(child)
            except (AssertionError, ProcessLookupError):
                self.stop(child, kill=True)
        for socket_path in self.sockets:
            socket_path.unlink(missing_ok=True)
            Path(str(socket_path) + ".lock").unlink(missing_ok=True)


def local_model_message(payload: dict) -> dict:
    messages = payload["messages"]
    goal = next(item["content"] for item in messages if item["role"] == "user")
    previous = [item for item in messages if item["role"] == "tool"]
    functions = [item["function"]["name"] for item in payload.get("tools", [])]
    if not functions:
        raise AssertionError("LangChain must send actual tool definitions")
    count = len(previous)
    if "MODEL_BUDGET" in goal:
        names = [functions[0]]
    elif "TOOL_BUDGET" in goal:
        names = [functions[0]] * (5 if count < 10 else 1)
    elif not previous:
        names = functions
    else:
        # Derive the report from the real tool evidence, never from a fixture answer.
        evidence = [json.loads(item["content"]) for item in previous]
        citations = [citation for item in evidence for citation in item.get("citations", [])]
        assert citations, "The real read-only tools returned no citations"
        snippets = "\n".join(f"[{item['id']}] {item['snippet']}" for item in citations)
        return {"role": "assistant", "content": (
            "## 问题摘要\n隔离诊断完成。\n## 已证实事实\n" + snippets
            + "\n## 可能原因及不确定性\n证据提示需要排查连接池，不能据此确定全局根因。"
            + "\n## 建议排查步骤\n检查连接池指标及操作手册。"
        )}
    calls = []
    for index, name in enumerate(names):
        arguments = {"minutes": 60, "keyword": MARKER} if name == "query_runtime_logs" else {"query": MARKER, "top_k": 4}
        calls.append({"id": f"call_{count}_{index}", "type": "function",
                      "function": {"name": name, "arguments": '{"minutes":' if "TRUNCATED_ARGS" in goal else json.dumps(arguments)}})
    content = (
        '公开说明\npassword="synthetic-line-one\nsynthetic-line-two\n"\n'
        'Cookie="synthetic-line-cookie-one\nsynthetic-line-cookie-two\n"\n'
        "Set-Cookie='synthetic-line-set-cookie-one\nsynthetic-line-set-cookie-two\n'\n"
        'Authorization="synthetic-line-auth-one\nsynthetic-line-auth-two\n"\n'
    ) if "MULTILINE_SECRET" in goal else None
    return {"role": "assistant", "content": content, "tool_calls": calls}


def local_model_chunks(message: dict):
    """Exercise the real SDK SSE parser, including fragmented function arguments."""
    content = message.get("content") or "正在核对所选数据源。\n将查询日志并对照知识库。\n"
    for line in content.splitlines(keepends=True):
        yield {"content": line}
    for index, call in enumerate(message.get("tool_calls", [])):
        arguments = call["function"]["arguments"]
        middle = len(arguments) // 2
        yield {"tool_calls": [{"index": index, "id": call["id"], "type": "function",
                              "function": {"name": call["function"]["name"], "arguments": arguments[:middle]}}]}
        yield {"tool_calls": [{"index": index, "function": {"arguments": arguments[middle:]}}]}


@contextmanager
def model_server(root: Path, port: int):
    observations = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                assert self.path == "/v1/chat/completions"
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                goal = next(item["content"] for item in payload["messages"] if item["role"] == "user")
                scenario = next((name for name in (
                    "CANCEL", "LATE", "CRASH", "REVOKE", "TIMEOUT", "RETRY", "LOGOUT", "CONCURRENT", "SAME", "BROWSER",
                ) if f"ACCEPT_{name}" in goal), None)
                with lock:
                    occurrence = 1 + sum(item["scenario"] == scenario for item in observations if "scenario" in item)
                    observations.append({"path": self.path, "scenario": scenario or "ordinary",
                                         "messages": len(payload["messages"]),
                                         "tools": [item["function"]["name"] for item in payload["tools"]]})
                blocking = scenario and (scenario != "LATE" or any(item["role"] == "tool" for item in payload["messages"]))
                if blocking:
                    barrier = f"{scenario}-{occurrence}" if scenario == "RETRY" else scenario
                    (root / f"model-entered-{barrier}").touch()
                    wait_until(lambda: (root / f"model-release-{barrier}").exists(), timeout=25, description=scenario)
                message = local_model_message(payload)
                if payload.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()

                    def send(delta, *, finish=None, usage=False):
                        chunk = {"id": "chatcmpl-isolated", "object": "chat.completion.chunk",
                                 "created": int(time.time()), "model": "isolated-agent",
                                 "choices": [] if usage else [{"index": 0, "delta": delta, "finish_reason": finish}]}
                        if usage:
                            chunk["usage"] = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
                        self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
                        self.wfile.flush()

                    send({"role": "assistant"})
                    for index, delta in enumerate(local_model_chunks(message)):
                        send(delta)
                        if "MULTILINE_SECRET" in goal and message.get("tool_calls") and index < 4:
                            time.sleep(0.8)
                        if scenario == "BROWSER" and message.get("tool_calls") and index < 2:
                            (root / f"stream-entered-BROWSER-{index + 1}").touch()
                            wait_until(lambda index=index: (root / f"stream-release-BROWSER-{index + 1}").exists(),
                                       timeout=25, description="browser streaming snapshot")
                    send({}, finish="tool_calls" if message.get("tool_calls") else "stop")
                    send({}, usage=True)
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    if blocking:
                        (root / f"model-returned-{scenario}").touch()
                    return
                response = {"id": "chatcmpl-isolated", "object": "chat.completion", "created": int(time.time()),
                            "model": "isolated-agent", "choices": [{"index": 0, "message": message,
                            "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
                body = json.dumps(response, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                if blocking:
                    (root / f"model-returned-{scenario}").touch()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                with lock:
                    observations.append({"error": str(error)})
                self.send_error(500)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield observations
    finally:
        for name in ("CANCEL", "LATE", "CRASH", "REVOKE", "TIMEOUT", "RETRY-1", "RETRY-2", "RETRY-3",
                     "LOGOUT", "CONCURRENT", "SAME", "BROWSER"):
            (root / f"model-release-{name}").touch()
        for index in (1, 2):
            (root / f"stream-release-BROWSER-{index}").touch()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        (root / "model-http.json").write_text(json.dumps(observations, indent=2))


def configure_child() -> Path:
    from runtime_acceptance import configure_child as isolate

    isolate()
    root = Path(os.environ["FASTCORE_INTEGRATION_ROOT"])
    validate_root(root)
    return root


def serve(port: int, mode: str):
    root = configure_child()
    import uvicorn

    from app.config.setting import settings
    from app.plugin.module_ai.agent import runtime
    from main import create_app

    # Production settings force CAPTCHA on; match the existing isolated HTTP suite.
    settings.CAPTCHA_ENABLE = False
    if mode == "crash":
        runtime.LEASE_SECONDS = 3
    if mode == "timeout":
        runtime.RUN_TIMEOUT = 1
    application = create_app()
    actual_lifespan = application.router.lifespan_context

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        async with actual_lifespan(app):
            assert runtime._workers, "Production manifest must start the agent worker"
            from sqlalchemy import update

            from app.api.v1.module_system.user.model import UserModel
            from app.core.database import async_db_session
            from app.core.logger import logger
            from app.utils.hash_bcrpy_util import PwdUtil

            if not (root / "admin-ready").exists():
                async with async_db_session() as db, db.begin():
                    await db.execute(update(UserModel).where(UserModel.username == "admin").values(password=PwdUtil.hash_password(PASSWORD)))
                (root / "admin-ready").touch()
            logger.warning(f"{MARKER}: connection pool wait exceeded; consult the isolated runbook")
            (root / f"worker-start-{os.getpid()}.json").write_text(json.dumps({
                "pid": os.getpid(), "mode": mode, "model_calls": runtime.MAX_MODEL_CALLS,
                "tool_calls": runtime.MAX_TOOL_CALLS, "timeout": runtime.RUN_TIMEOUT,
                "lease": runtime.LEASE_SECONDS, "attempts": runtime.MAX_ATTEMPTS,
            }))
            yield
        assert not runtime._workers and runtime._redis is None
        (root / f"shutdown-{os.getpid()}").touch()

    application.router.lifespan_context = lifespan
    uvicorn.run(application, host="127.0.0.1", port=port, log_level="warning")


def provision(root: Path, database_type: str, processes: Processes):
    from redis import Redis

    ports = set()
    while len(ports) < 4:
        ports.add(free_port())
    database_port, redis_port, api_port, model_port = ports
    from runtime_acceptance import validate_target

    validate_target(database_port, redis_port, True)
    environment = clean_environment()
    database_dir = root / "database"
    if database_type == "mysql":
        with (root / "database-init.log").open("w") as log:
            initialized = run_isolated([
                str(MYSQL / "mysqld"), "--no-defaults", "--initialize-insecure",
                f"--basedir={MYSQL.parent}", f"--datadir={database_dir}",
            ], env=environment, stdout=log, stderr=log, timeout=90)
        assert initialized.returncode == 0, "Temporary MySQL initialization failed"
        mysql_socket = Path(f"/tmp/fastcore-agent-{secrets.token_hex(8)}.sock")
        processes.sockets.append(mysql_socket)
        child = processes.start("mysql", [
            str(MYSQL / "mysqld"), "--no-defaults", f"--basedir={MYSQL.parent}", f"--datadir={database_dir}",
            "--bind-address=127.0.0.1", f"--port={database_port}", f"--socket={mysql_socket}",
            "--mysqlx=OFF", f"--pid-file={root / 'mysql.pid'}",
        ], environment)
        user = "root"
    else:
        with (root / "database-init.log").open("w") as log:
            initialized = run_isolated([
                str(POSTGRES / "initdb"), "-D", str(database_dir), "-U", "fastcore_ci_runner",
                "--auth=trust", "--no-locale", "--encoding=UTF8",
            ], env=environment, stdout=log, stderr=log, timeout=90)
        assert initialized.returncode == 0, "Temporary PostgreSQL initialization failed"
        child = processes.start("postgresql", [
            str(POSTGRES / "postgres"), "-D", str(database_dir), "-h", "127.0.0.1",
            "-p", str(database_port), "-c", "unix_socket_directories=",
        ], environment)
        user = "fastcore_ci_runner"
    redis_child = processes.start("redis", [
        "/opt/homebrew/bin/redis-server", "--bind", "127.0.0.1", "--port", str(redis_port),
        "--save", "", "--appendonly", "no", "--dir", str(root),
    ], environment)
    database_name = "fastcore_ci_" + secrets.token_hex(8)

    def connect():
        assert child.poll() is None, "Temporary database exited"
        try:
            if database_type == "mysql":
                import pymysql

                return pymysql.connect(host="127.0.0.1", port=database_port, user=user, autocommit=True)
            import psycopg

            return psycopg.connect(host="127.0.0.1", port=database_port, user=user, dbname="postgres", autocommit=True)
        except Exception:
            return None

    connection = wait_until(connect, description="temporary database")
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{database_name}"' if database_type == "postgresql"
                           else f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4")
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
    finally:
        connection.close()
    redis = Redis(host="127.0.0.1", port=redis_port, db=15)
    try:
        def ready():
            assert redis_child.poll() is None
            try:
                return redis.ping()
            except Exception:
                return False
        wait_until(ready, description="temporary Redis")
        assert redis.dbsize() == 0
        redis_version = redis.info("server")["redis_version"]
    finally:
        redis.close()
    avatar = root / "static" / "image" / "avatar.png"
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes((BACKEND / "static" / "image" / "avatar.png").read_bytes())
    environment.update({
        "ENVIRONMENT": "prod", "DATABASE_TYPE": database_type, "DATABASE_HOST": "127.0.0.1",
        "DATABASE_PORT": str(database_port), "DATABASE_USER": user, "DATABASE_PASSWORD": "",
        "DATABASE_NAME": database_name, "REDIS_ENABLE": "true", "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": str(redis_port), "REDIS_USER": "", "REDIS_PASSWORD": "", "REDIS_DB_NAME": "15",
        "SECRET_KEY": secrets.token_urlsafe(48), "SQL_DB_ENABLE": "true", "STATIC_ENABLE": "true",
        "STATIC_ROOT": str(root / "static"),
        "UPLOAD_FILE_PATH": str(root / "uploads"), "ROOT_PATH": "", "CAPTCHA_ENABLE": "false",
        "RATE_LIMITER_TIMES": "10000", "RATE_LIMITER_SECONDS": "1", "IP_LOCATION_ENABLE": "false",
        "OPENAI_API_KEY": "isolated-local-only", "OPENAI_MODEL": "isolated-agent",
        "OPENAI_BASE_URL": f"http://127.0.0.1:{model_port}/v1", "MODEL_ALLOWED_HOSTS": '["127.0.0.1"]',
        "RETRIEVAL_MODE": "bm25", "BM25_TOKENIZER": "char", "CHROMA_PERSIST_DIR": str(root / "chroma"),
        "BM25_INDEX_DIR": str(root / "bm25"), "LOCAL_EMBEDDING_CACHE_DIR": str(root / "embeddings"),
        "ANONYMIZED_TELEMETRY": "False", "FASTCORE_INTEGRATION_ROOT": str(root),
        "FASTCORE_AGENT_BROWSER_BARRIER": "1",
        "PYTHONPATH": os.pathsep.join([str(BACKEND / "integration"), str(BACKEND)]),
    })
    return environment, api_port, model_port, {
        "database": database_type, "database_version": version, "redis_version": redis_version,
        "ports": {"database": database_port, "redis": redis_port, "http": api_port, "model": model_port},
        "service_pids": [child.pid, redis_child.pid],
    }


def verify(root: Path, environment: dict, port: int, processes: Processes, *, browser: bool) -> dict:
    import httpx
    from sqlalchemy import URL, create_engine, text

    url = URL.create("mysql+pymysql" if environment["DATABASE_TYPE"] == "mysql" else "postgresql+psycopg",
                     username=environment["DATABASE_USER"], host="127.0.0.1",
                     port=int(environment["DATABASE_PORT"]), database=environment["DATABASE_NAME"])
    engine = create_engine(url)
    checks = []
    result = {"passed": False, "checks": checks}
    child = None
    headers = {}
    index = 0

    def save():
        (root / "agent-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))

    def passed(name, evidence):
        checks.append({"name": name, "status": "PASS", "evidence": evidence})
        save()

    def data(response):
        assert response.status_code == 200, f"{response.request.method} {response.request.url.path}: {response.status_code}: {response.text[:500]}"
        payload = response.json()
        assert payload["success"] is True, payload.get("msg")
        return payload["data"]

    def start(mode="normal"):
        nonlocal child, index
        index += 1
        child = processes.start(f"backend-{index}", [sys.executable, str(SCRIPT), "--serve", str(port), "--mode", mode], environment)

        def ready():
            assert child.poll() is None, f"Backend exited; see backend-{index}.log"
            try:
                response = client.get("/common/health/ready")
                if response.status_code == 200:
                    dependencies = response.json()["data"]["dependencies"]
                    assert dependencies["database"]["status"] == dependencies["redis"]["status"] == 1
                    return True
            except httpx.TransportError:
                pass
        wait_until(ready, timeout=100, description="production lifespan and migration")

    def stop(*, kill=False):
        processes.stop(child, kill=kill)
        if not kill:
            assert (root / f"shutdown-{child.pid}").exists(), "Production shutdown did not finish"

    def login(username, password=PASSWORD):
        token = data(client.post("/system/auth/login", data={"username": username, "password": password}))["access_token"]
        return {"Authorization": "Bearer " + token}

    def create(goal, *, bases=None, logs=True, auth=None, request_id=None):
        return data(client.post("/ai/agent/runs", headers=headers if auth is None else auth, json={
            "request_id": request_id or secrets.token_hex(12), "goal": goal,
            "knowledge_base_ids": bases or [], "use_runtime_logs": logs,
        }))

    def detail(run_id, auth=None, after=0):
        return data(client.get(f"/ai/agent/runs/{run_id}", headers=headers if auth is None else auth, params={"after_seq": after}))

    def terminal(run_id, status="succeeded", auth=None):
        def finished():
            current = detail(run_id, auth)
            if current["status"] in {"failed", "succeeded", "cancelled"}:
                assert current["status"] == status, current
                return current
        return wait_until(finished, timeout=50, description=f"agent {status}")

    def upload_base(name, auth):
        base = data(client.post("/ai/knowledge/create", headers=auth, json={"name": name, "is_enabled": True}))
        document = data(client.post("/ai/knowledge/document/upload", headers=auth, data={"knowledge_base_id": base["id"]},
                                   files={"file": ("agent-runbook.txt", f"{MARKER}：连接池超时请检查活跃连接与等待队列。".encode(), "text/plain")}))
        def indexed():
            items = data(client.get("/ai/knowledge/document/list", headers=auth, params={"knowledge_base_id": base["id"]}))["items"]
            item = next(item for item in items if item["id"] == document["id"])
            assert item["index_status"] != "failed", item
            return item if item["index_status"] == "success" else None
        wait_until(indexed, timeout=60, description="real document indexing")
        return base["id"]

    def gate(name):
        wait_until(lambda: (root / f"model-entered-{name}").exists(), description=f"model {name} barrier")

    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False) as client:
            start()
            headers = login("admin")
            assert client.get("/ai/agent/runs").status_code == 401
            defaults = json.loads((root / f"worker-start-{child.pid}.json").read_text())
            assert {key: defaults[key] for key in ("model_calls", "tool_calls", "attempts", "timeout", "lease")} == {
                "model_calls": 6, "tool_calls": 10, "attempts": 3, "timeout": 180, "lease": 210,
            }
            for invalid in (
                {"request_id": "invalid", "goal": " "},
                {"request_id": "../path", "goal": "diagnose"},
                {"request_id": "invalid", "goal": "diagnose", "use_runtime_logs": "true"},
                {"request_id": "invalid", "goal": "diagnose", "knowledge_base_ids": [], "use_runtime_logs": False},
            ):
                assert client.post("/ai/agent/runs", headers=headers, json=invalid).status_code == 422
            base_id = upload_base("Agent 隔离知识库", headers)
            first = create(f"分析 {MARKER} 并结合日志和手册给出证据", bases=[base_id], request_id="idempotent")
            duplicate = create(first["goal"], bases=[base_id], request_id="idempotent")
            assert duplicate["run_id"] == first["run_id"]
            conflict = client.post("/ai/agent/runs", headers=headers, json={
                "request_id": "idempotent", "goal": "different", "knowledge_base_ids": [base_id], "use_runtime_logs": True})
            assert conflict.status_code == 409
            completed = terminal(first["run_id"])
            assert MARKER in completed["result"] and "[1]" in completed["result"]
            assert {item["source"] for item in completed["citations"]} == {"runtime-log", "knowledge-base"}
            assert completed["model_calls"] == 2 and completed["tool_calls"] == 2
            sequences = [item["seq"] for item in completed["events"]]
            assert sequences == list(range(1, completed["last_seq"] + 1))
            assert {item["data"].get("name") for item in completed["events"] if item["type"] == "tool_completed"} == {"query_runtime_logs", "search_knowledge"}
            tail = detail(first["run_id"], after=completed["last_seq"] - 1)
            assert len(tail["events"]) == 1
            assert all(item["result"] is None and not item["citations"] for item in data(client.get("/ai/agent/runs", headers=headers)))
            with engine.connect() as db:
                assert db.execute(text("SELECT count(*) FROM ai_agent_run WHERE request_id='idempotent'")).scalar_one() == 1
                assert db.execute(text("SELECT count(*) FROM ai_agent_event WHERE run_id=:id"), {"id": first["run_id"]}).scalar_one() == completed["last_seq"]
                usage = [dict(row) for row in db.execute(text(
                    "SELECT id, purpose, status, input_tokens, output_tokens, total_tokens, day "
                    "FROM ai_usage_record WHERE user_id=(SELECT user_id FROM ai_agent_run WHERE run_id=:id) ORDER BY id"
                ), {"id": first["run_id"]}).mappings()]
                assert len(usage) == 2 and all(
                    row["purpose"] == "agent" and row["status"] == "succeeded"
                    and all(row[key] is not None and row[key] > 0 for key in ("input_tokens", "output_tokens", "total_tokens"))
                    for row in usage
                ), usage
                daily_calls = db.execute(text(
                    "SELECT calls FROM ai_usage_daily WHERE user_id=(SELECT user_id FROM ai_agent_run WHERE run_id=:id) AND day=:day"
                ), {"id": first["run_id"], "day": usage[0]["day"]}).scalar_one()
                assert daily_calls >= 2
                menu_ids = db.execute(text("SELECT id FROM platform_menu WHERE permission IN "
                                          "('module_ai:chat:ws','module_ai:knowledge:create','module_ai:knowledge:query',"
                                          "'module_ai:document:create','module_ai:document:query')")).scalars().all()
            passed("HTTP login, idempotency, actual tools, citations, ordered SQL events", completed)
            passed("real agent usage ledger and daily quota counter", {
                "records": [{**row, "day": str(row["day"])} for row in usage], "daily_calls": daily_calls,
            })
            cancel_completed = data(client.post(f"/ai/agent/runs/{first['run_id']}/cancel", headers=headers))
            assert cancel_completed["status"] == "succeeded"
            assert cancel_completed["result"] is None and cancel_completed["citations"] == []
            assert detail(first["run_id"])["result"] == completed["result"]
            passed("cancel of completed task returns only safe summary and preserves stored report", {"run_id": first["run_id"]})
            truncated = terminal(create(f"TRUNCATED_ARGS {MARKER}")["run_id"], status="failed")
            assert truncated["tool_calls"] == 0 and truncated["model_calls"] == 1
            assert "完整" in truncated["error"]
            assert not any(item["type"] == "tool_started" for item in truncated["events"])
            passed("truncated provider SSE arguments fail before any real tool executes", truncated)
            multiline = terminal(create(f"MULTILINE_SECRET {MARKER}")["run_id"])
            assert "synthetic-line-" not in json.dumps(multiline)
            analyses = [item["data"] for item in multiline["events"] if item["type"] == "analysis"]
            assert len(analyses) >= 2 and any("[REDACTED]" in item["text"] for item in analyses)
            with engine.connect() as db:
                stored = db.execute(text("SELECT data FROM ai_agent_event WHERE run_id=:id"),
                                    {"id": multiline["run_id"]}).scalars().all()
                assert "synthetic-line-" not in json.dumps(stored)
            passed("multiline sensitive values never enter SQL analysis snapshots during SSE", multiline)

            def concurrent_submissions(same_id):
                barrier = threading.Barrier(6)
                def submit(index):
                    barrier.wait(timeout=10)
                    return client.post("/ai/agent/runs", headers=headers, json={
                        "request_id": "concurrent-same" if same_id else f"concurrent-{index}",
                        "goal": f"ACCEPT_{'SAME' if same_id else 'CONCURRENT'} {MARKER}",
                        "knowledge_base_ids": [base_id], "use_runtime_logs": True,
                    })
                with ThreadPoolExecutor(max_workers=6) as pool:
                    responses = list(pool.map(submit, range(6)))
                successful = [data(response) for response in responses if response.status_code == 200]
                statuses = sorted(response.status_code for response in responses)
                assert statuses == ([200] * 6 if same_id else [200] * 3 + [409] * 3), statuses
                run_ids = {item["run_id"] for item in successful}
                assert len(run_ids) == (1 if same_id else 3)
                with engine.connect() as db:
                    count = db.execute(text("SELECT count(*) FROM ai_agent_run WHERE request_id " +
                                            ("= 'concurrent-same'" if same_id else "IN ('concurrent-0','concurrent-1','concurrent-2','concurrent-3','concurrent-4','concurrent-5')"))).scalar_one()
                assert count == len(run_ids)
                gate("SAME" if same_id else "CONCURRENT")
                for run_id in run_ids:
                    assert data(client.post(f"/ai/agent/runs/{run_id}/cancel", headers=headers))["status"] == "cancelled"
                (root / f"model-release-{'SAME' if same_id else 'CONCURRENT'}").touch()
                passed("concurrent same request id creates exactly one SQL row" if same_id else
                       "concurrent active limit admits exactly three of six HTTP submissions",
                       {"statuses": statuses, "sql_rows": count, "run_ids": sorted(run_ids)})
            concurrent_submissions(False)
            concurrent_submissions(True)

            role = data(client.post("/system/role/create", headers=headers, json={"name": "Agent owner", "code": "agent_owner", "data_scope": 1}))
            data(client.put("/system/role/permission", headers=headers, json={"role_ids": [role["id"]], "menu_ids": menu_ids, "data_scope": 1}))
            for username, roles in (("agent-owner", [role["id"]]), ("agent-other", [role["id"]]), ("agent-denied", [])):
                data(client.post("/system/user/create", headers=headers, json={"username": username, "name": username, "password": PASSWORD, "role_ids": roles}))
            owner, other, denied = (login(name) for name in ("agent-owner", "agent-other", "agent-denied"))
            assert client.get("/ai/agent/runs", headers=denied).status_code == 403
            assert client.post("/ai/agent/runs", headers=owner, json={"request_id": "log-denied", "goal": "logs", "use_runtime_logs": True}).status_code == 403
            assert client.post("/ai/agent/runs", headers=owner, json={"request_id": "scope-denied", "goal": MARKER, "use_runtime_logs": False, "knowledge_base_ids": [base_id]}).status_code == 403
            own_base = upload_base("普通用户自己的知识库", owner)
            own = create(MARKER, bases=[own_base], logs=False, auth=owner)
            own_done = terminal(own["run_id"], auth=owner)
            assert {item["source"] for item in own_done["citations"]} == {"knowledge-base"}
            for unauthorized in (other, headers):
                assert client.get(f"/ai/agent/runs/{own['run_id']}", headers=unauthorized).status_code == 404
                assert client.post(f"/ai/agent/runs/{own['run_id']}/cancel", headers=unauthorized).status_code == 404
                assert all(item["run_id"] != own["run_id"] for item in data(client.get("/ai/agent/runs", headers=unauthorized)))
            passed("normal user permissions, own data scope, strict owner (including superadmin)", {"run_id": own["run_id"], "statuses": [401, 403, 404]})

            stop()
            start()
            restored = detail(first["run_id"])
            for field in ("result", "citations", "events", "last_seq", "attempts", "model_calls", "tool_calls"):
                assert restored[field] == completed[field], field
            passed("normal server restart and HTTP refresh preserve exact result and events", {"run_id": first["run_id"], "restart_pid": child.pid})

            cancelled = create(f"ACCEPT_CANCEL {MARKER}")
            gate("CANCEL")
            cancelled = data(client.post(f"/ai/agent/runs/{cancelled['run_id']}/cancel", headers=headers))
            assert cancelled["status"] == "cancelled"
            (root / "model-release-CANCEL").touch()
            wait_until(lambda: (root / "model-returned-CANCEL").exists(), description="late provider response")
            time.sleep(1.5)
            late = detail(cancelled["run_id"])
            assert late["status"] == "cancelled" and late["result"] is None and late["tool_calls"] == 0
            assert late["last_seq"] == cancelled["last_seq"]
            passed("cancel fences late real HTTP model response", late)

            final_pending = create(f"ACCEPT_LATE {MARKER}", bases=[base_id])
            gate("LATE")
            before_cancel = detail(final_pending["run_id"])
            assert before_cancel["tool_calls"] == 2 and before_cancel["result"] is None
            cancelled_final = data(client.post(f"/ai/agent/runs/{final_pending['run_id']}/cancel", headers=headers))
            assert cancelled_final["result"] is None and cancelled_final["citations"] == []
            (root / "model-release-LATE").touch()
            wait_until(lambda: (root / "model-returned-LATE").exists(), description="late final diagnostic report")
            time.sleep(1.5)
            final_late = detail(final_pending["run_id"])
            assert final_late["status"] == "cancelled" and final_late["result"] is None
            assert final_late["last_seq"] == cancelled_final["last_seq"] and final_late["tool_calls"] == 2
            passed("cancel after both real tools rejects late final report and further events", final_late)

            for label, expected_calls in (("MODEL_BUDGET", 6), ("TOOL_BUDGET", 3)):
                bounded = terminal(create(f"{label} {MARKER}")["run_id"], status="failed")
                assert bounded["model_calls"] == expected_calls and bounded["tool_calls"] <= 10
                if label == "TOOL_BUDGET":
                    assert bounded["tool_calls"] == 10
                passed(label, bounded)

            revoke = create(f"ACCEPT_REVOKE {MARKER}", bases=[own_base], logs=False, auth=owner)
            gate("REVOKE")
            data(client.put("/system/role/permission", headers=headers, json={"role_ids": [role["id"]], "menu_ids": [], "data_scope": 1}))
            (root / "model-release-REVOKE").touch()
            def revoked():
                with engine.connect() as db:
                    row = db.execute(text("SELECT status, result, tool_calls FROM ai_agent_run WHERE run_id=:id"), {"id": revoke["run_id"]}).one()
                return dict(row._mapping) if row.status == "failed" else None
            revoked_result = wait_until(revoked, description="online permission revocation")
            assert revoked_result["result"] is None and revoked_result["tool_calls"] == 0
            passed("online permission revocation prevents publication and new tools", revoked_result)

            temporary_session = login("admin")
            logged_out = create(f"ACCEPT_LOGOUT {MARKER}", auth=temporary_session)
            gate("LOGOUT")
            data(client.post("/system/auth/logout", headers=temporary_session))
            assert client.get("/ai/agent/runs", headers=temporary_session).status_code == 401
            (root / "model-release-LOGOUT").touch()
            logged_out = terminal(logged_out["run_id"], status="failed")
            assert logged_out["tool_calls"] == 0 and logged_out["result"] is None
            passed("real Redis logout fences running log task before tool execution", logged_out)

            stop()
            start("crash")
            crashed = create(f"ACCEPT_CRASH {MARKER}", bases=[base_id])
            gate("CRASH")
            with engine.connect() as db:
                before = dict(db.execute(text("SELECT status, attempts, token, lease_until FROM ai_agent_run WHERE run_id=:id"),
                                         {"id": crashed["run_id"]}).one()._mapping)
            assert before["status"] == "running" and before["attempts"] == 1 and before["token"]
            killed_pid = child.pid
            stop(kill=True)
            assert child.returncode == -signal.SIGKILL
            (root / "model-release-CRASH").touch()
            time.sleep(3.2)
            start()
            recovered = terminal(crashed["run_id"])
            assert recovered["attempts"] == 2 and recovered["model_calls"] == 3 and recovered["tool_calls"] == 2
            assert any(event["type"] == "recovering" for event in recovered["events"])
            passed("SIGKILL, real expired SQL lease, production hook recovery", {
                "killed_pid": killed_pid, "before": {
                    "status": before["status"], "attempts": before["attempts"],
                    "has_lease_token": bool(before["token"]), "lease_until": str(before["lease_until"]),
                }, "after": recovered,
            })

            stop()
            start("crash")
            retry = create(f"ACCEPT_RETRY {MARKER}")
            killed = []
            for attempt in range(1, 4):
                gate(f"RETRY-{attempt}")
                current = detail(retry["run_id"])
                assert current["attempts"] == attempt and current["model_calls"] == attempt
                killed.append(child.pid)
                stop(kill=True)
                (root / f"model-release-RETRY-{attempt}").touch()
                time.sleep(3.2)
                start("crash" if attempt < 3 else "normal")
            exhausted = terminal(retry["run_id"], status="failed")
            assert exhausted["attempts"] == 3 and exhausted["model_calls"] == 3 and exhausted["tool_calls"] == 0
            assert "多次中断" in exhausted["error"]
            passed("MAX_ATTEMPTS: three real SIGKILLs stop recovery at three attempts", {"killed_pids": killed, "run": exhausted})
            stop()
            start("timeout")
            timed = terminal(create(f"ACCEPT_TIMEOUT {MARKER}")["run_id"], status="failed")
            assert "超时" in timed["error"]
            (root / "model-release-TIMEOUT").touch()
            passed("run timeout (one-second isolated fault injection)", timed)
            stop()
            start()
            persisted = detail(recovered["run_id"])
            assert persisted["attempts"] == 2 and persisted["last_seq"] == recovered["last_seq"]
            passed("second restart does not duplicate recovered work", {"run_id": recovered["run_id"], "attempts": 2})

            if browser:
                with (root / "browser.log").open("w") as log:
                    browser_result = run_isolated(
                        ["node", str(BACKEND / "integration" / "agent_browser_checks.cjs"), str(port)],
                        cwd=BACKEND, env=environment, stdout=log, stderr=log, timeout=180)
                assert browser_result.returncode == 0, "Chrome walk-through failed; see browser.log"
                passed("Chrome menu, diagnostic creation, steps, citations, refresh, 390px",
                       json.loads((root / "agent-browser-results.json").read_text()))
            else:
                checks.append({"name": "Chrome", "status": "NOT RUN", "reason": "--skip-browser"})
            stop()
            result["passed"] = True
    except Exception as error:
        result["error"] = str(error)
        save()
        raise
    finally:
        engine.dispose()
        save()
    return result


def run(database_types: list[str], *, browser=True) -> Path:
    suite = Path(tempfile.mkdtemp(prefix="fastcore-integration-agent-suite-"))
    os.chmod(suite, 0o700)
    print(f"Agent acceptance artifacts: {suite}", flush=True)
    summary = {"passed": False, "databases": [], "boundary": (
        "Real private database daemons, Redis, production startup/shutdown and migrations, HTTP auth, "
        "LangChain/OpenAI HTTP tools, real log reader and BM25; deterministic local provider, disabled CAPTCHA; "
        "only crash subprocess lease and timeout subprocess deadline shortened. No external model or embeddings."
    )}
    try:
        for database_type in database_types:
            root = Path(tempfile.mkdtemp(prefix=f"fastcore-integration-agent-{database_type}-", dir=suite))
            processes = Processes(root)
            row = {"database": database_type, "artifacts": str(root), "passed": False}
            summary["databases"].append(row)
            try:
                environment, api_port, model_port, metadata = provision(root, database_type, processes)
                (root / "environment").mkdir()
                source_files = [*BACKEND.glob("app/plugin/module_ai/agent/*.py"),
                                BACKEND / "app/plugin/module_ai/chat/public.py",
                                BACKEND / "app/plugin/module_ai/chat/rag.py",
                                BACKEND / "app/plugin/module_ai/chat/runtime_logs.py",
                                BACKEND / "app/plugin/module_ai/chat/usage.py",
                                BACKEND / "app/plugin/module_ai/plugin.toml",
                                BACKEND / "app/alembic/versions/20261024_agent_runs.py",
                                BACKEND.parent / "frontend/src/views/module_ai/chat/components/FaDiagnosticAgent.vue",
                                BACKEND.parent / "frontend/src/views/module_ai/chat/components/diagnosticAgentTimeline.ts",
                                BACKEND.parent / "frontend/src/views/module_ai/chat/index.vue",
                                BACKEND.parent / "frontend/src/api/module_ai/agent.ts", SCRIPT,
                                BACKEND / "integration/agent_browser_checks.cjs"]
                metadata["source_sha256"] = {str(path.relative_to(BACKEND.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
                                             for path in source_files if path.is_file()}
                metadata["head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=BACKEND, text=True).strip()
                (root / "manifest.json").write_text(json.dumps(metadata, indent=2))
                with model_server(root, model_port):
                    evidence = verify(root, environment, api_port, processes, browser=browser)
                isolated = [json.loads(path.read_text()) for path in root.glob("isolation-*.json")]
                assert any(item["module_entry"] for item in isolated), "Migration subprocess isolation evidence missing"
                assert all(Path(item["environment"]).resolve() == (root / "environment").resolve() for item in isolated), (
                    "A subprocess loaded environment files outside its disposable root"
                )
                row["passed"] = evidence["passed"]
                row["isolation_processes"] = len(isolated)
                print(f"{database_type}: PASS ({len(evidence['checks'])} checks)", flush=True)
            except Exception as error:
                row["error"] = str(error)
                print(f"{database_type}: FAIL: {error}", flush=True)
            finally:
                processes.close()
                row["services_reaped"] = all(child.poll() is not None for child in processes.children)
                if (root / "manifest.json").exists():
                    manifest = json.loads((root / "manifest.json").read_text())
                    manifest["source_sha256_end"] = {
                        name: hashlib.sha256((BACKEND.parent / name).read_bytes()).hexdigest()
                        for name in manifest["source_sha256"] if (BACKEND.parent / name).is_file()
                    }
                    manifest["changed_during_run"] = [
                        name for name, digest in manifest["source_sha256"].items()
                        if manifest["source_sha256_end"].get(name) != digest
                    ]
                    manifest["process_exit_codes"] = {str(child.pid): child.returncode for child in processes.children}
                    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
                (suite / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        summary["passed"] = all(row["passed"] for row in summary["databases"])
    finally:
        (suite / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["passed"]:
        raise RuntimeError(f"Agent acceptance incomplete; see {suite / 'results.json'}")
    return suite


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-type", choices=("mysql", "postgresql", "both"), default="both")
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("normal", "crash", "timeout"), default="normal", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.serve:
        serve(arguments.serve, arguments.mode)
    else:
        run(["mysql", "postgresql"] if arguments.database_type == "both" else [arguments.database_type],
            browser=not arguments.skip_browser)
