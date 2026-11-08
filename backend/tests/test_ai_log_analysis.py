import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from app.core.security import credential_fingerprint
from app.plugin.module_ai.chat import public, rag, runtime_logs, service
from app.plugin.module_ai.chat.schema import ChatQuerySchema
from tests.test_auth_session import FakeRedis

pytest_plugins = ("ai_usage_fixtures",)


@pytest.fixture
def log_chat(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_logs, "LOG_DIR", tmp_path)
    monkeypatch.setattr(service, "load_runtime_chat_model_config", AsyncMock())
    monkeypatch.setattr(service.ChatService, "_validate_ai_config", staticmethod(AsyncMock(return_value=None)))
    user = SimpleNamespace(id=7, is_superuser=True, password="fixture-password")
    redis = FakeRedis()
    redis.values["user_session:isolated-log-session"] = json.dumps({
        "user_id": user.id,
        "credential_fingerprint": credential_fingerprint(user.id, user.password),
    }).encode()
    auth = SimpleNamespace(
        user=user,
        session_id="isolated-log-session",
        db=SimpleNamespace(scalar=AsyncMock(return_value=7), rollback=AsyncMock()),
        redis=redis,
    )
    auth.redis.exists = AsyncMock(return_value=True)
    auth.redis.set = AsyncMock(wraps=auth.redis.set)
    monkeypatch.setattr(public, "_load_authenticated_user", AsyncMock(return_value=user))
    captured = {"calls": 0}

    class Model:
        async def request_tools(self, messages, tools):
            captured["calls"] += 1
            captured["messages"] = list(messages)
            captured["tools"] = tools
            if isinstance(messages[-1], ToolMessage):
                captured["prompt"] = messages[-1].content
                return AIMessage(content="初步判断：检索失败 [1]。api_key=split-secret-value")
            return AIMessage(content="", tool_calls=[{"name": "query_runtime_logs", "args": {}, "id": "log-1"}])

    monkeypatch.setattr(rag, "get_cached_chat_model", Model)
    current = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:23]
    (tmp_path / "fastcore.log").write_text(
        f"{current} | ERROR    | app.knowledge:index:42 - LOG-ACCEPTANCE-42 retrieval failed token=fixture-secret | cid=abcd1234\n",
        encoding="utf-8",
    )
    return auth, captured


async def collect(auth, **overrides):
    query = ChatQuerySchema(message="请分析错误", request_id="log-check", log_query={}, **overrides)
    return [event async for event in service.ChatService(auth).chat_query(query, structured=True)]


async def test_multiple_calls_and_continued_investigation_return_all_tool_results(log_chat, monkeypatch):
    auth, _ = log_chat
    rounds = 0

    class Model:
        async def request_tools(self, messages, tools):
            nonlocal rounds
            rounds += 1
            tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
            assert len(tool_messages) == max(0, rounds - 1) + int(rounds > 1)
            assert len({message.tool_call_id for message in tool_messages}) == len(tool_messages)
            if rounds == 5:
                return AIMessage(content="完整分析 [1] " + "说明" * 13000)
            calls = [{"name": "query_runtime_logs", "args": {"keyword": "LOG-ACCEPTANCE-42"}, "id": f"round-{rounds}-" + "a" * 150}]
            if rounds == 1:
                calls.append({"name": "query_runtime_logs", "args": {"levels": ["INFO"]}, "id": "batch-info"})
            return AIMessage(content="", tool_calls=calls)

    monkeypatch.setattr(rag, "get_cached_chat_model", Model)
    events = await collect(auth)
    assert events[-1]["type"] == "done"
    assert len([event for event in events if event["type"] == "tool_call"]) == 5
    assert len([event for event in events if event["type"] == "log_result"]) == 5
    assert len(next(event["content"] for event in events if event["type"] == "chunk")) > 24000


async def test_entire_tool_batch_is_validated_before_reading(log_chat, monkeypatch):
    auth, _ = log_chat
    reader = AsyncMock()
    monkeypatch.setattr(service, "query_runtime_logs", reader)
    calls = [{"name": "query_runtime_logs", "args": {}, "id": "valid"}, {"name": "run_shell", "args": {}, "id": "invalid"}]
    monkeypatch.setattr(rag, "get_cached_chat_model", lambda: SimpleNamespace(request_tools=AsyncMock(return_value=AIMessage(content="", tool_calls=calls))))
    events = await collect(auth)
    assert events[-1]["error_code"] == "invalid_tool_call"
    reader.assert_not_called()


async def test_log_analysis_has_redacted_evidence_without_history_or_memory(log_chat, monkeypatch):
    auth, captured = log_chat
    create = AsyncMock(side_effect=AssertionError("Log analysis must not create a chat session"))
    memory = AsyncMock(side_effect=AssertionError("Log analysis must not extract memory"))
    monkeypatch.setattr(service.ChatSessionCRUD, "create_crud", create)
    monkeypatch.setattr(service.ChatService, "_trigger_memory_extraction", memory)
    events = await collect(auth)
    assert [event["type"] for event in events] == ["stage", "stage", "tool_call", "log_result", "citations", "stage", "chunk", "done"]
    assert [event["stage"] for event in events if event["type"] == "stage"] == ["routing", "querying_logs", "analyzing"]
    assert "LOG-ACCEPTANCE-42" in captured["prompt"]
    assert "fixture-secret" not in captured["prompt"]
    assert "[REDACTED]" in captured["prompt"]
    assert "不可信" in captured["messages"][0].content
    assert "不需要则仅回复 NO_LOG_TOOL，不回答原问题" in captured["messages"][0].content
    assert captured["messages"][0].type == "system"
    assert isinstance(captured["messages"][-1], ToolMessage)
    assert captured["messages"][-1].tool_call_id == "log-1"
    assert captured["tools"][0]["function"]["name"] == "query_runtime_logs"
    assert "split-secret-value" not in events[-2]["content"]
    assert next(event for event in events if event["type"] == "log_result")["returned_count"] == 1
    assert next(event for event in events if event["type"] == "citations")["citations"][0]["source"] == "runtime-log"
    assert "session_id" not in events[-1]
    assert captured["calls"] == 2
    create.assert_not_called()
    memory.assert_not_called()
    assert auth.db.scalar.await_count == 4


async def test_chat_log_evidence_is_bounded_before_model_context(log_chat, monkeypatch):
    auth, captured = log_chat
    count = service.MAX_CHAT_LOG_ENTRIES + 5
    entries = [
        {
            "id": str(index),
            "timestamp": "2026-10-03T12:00:00.000",
            "level": "ERROR",
            "correlation_id": "",
            "file": "fastcore.log",
            "message": "evidence-" + ("x" * (service.MAX_CHAT_LOG_ENTRY_CHARS + 1000)),
        }
        for index in range(count)
    ]
    monkeypatch.setattr(service, "query_runtime_logs", AsyncMock(return_value={
        "source": "fastcore.log", "start_time": "start", "end_time": "end",
        "scanned_bytes": 1, "scanned_files": ["fastcore.log"], "matched_count": count,
        "truncated": False, "notice": "notice", "entries": entries,
    }))
    events = await collect(auth)
    assert events[-1]["type"] == "done"
    result = next(event for event in events if event["type"] == "log_result")
    assert result["returned_count"] == service.MAX_CHAT_LOG_ENTRIES and result["truncated"] is True
    payload = json.loads(captured["messages"][-1].content)
    assert len(payload["entries"]) == service.MAX_CHAT_LOG_ENTRIES
    assert all(len(entry["message"]) <= service.MAX_CHAT_LOG_ENTRY_CHARS for entry in payload["entries"])


@pytest.mark.parametrize("scenario", ["ordinary", "logged-out", "missing-session", "revoked", "unavailable"])
async def test_access_checks_reject_before_log_read_or_model(log_chat, monkeypatch, scenario):
    auth, captured = log_chat
    query_logs = AsyncMock(side_effect=AssertionError("Must reject before reading logs"))
    monkeypatch.setattr(service, "query_runtime_logs", query_logs)
    if scenario == "ordinary":
        auth.user.is_superuser = False
    elif scenario == "logged-out":
        auth.redis.exists.return_value = False
    elif scenario == "missing-session":
        auth.session_id = None
    elif scenario == "revoked":
        auth.db.scalar.return_value = None
    elif scenario == "unavailable":
        auth.redis.exists.side_effect = RuntimeError("private-infrastructure-secret")
    events = await collect(auth)
    assert events[-1]["type"] == "error"
    assert [event["type"] for event in events] == ["stage", "error"]
    assert "private-infrastructure-secret" not in events[-1]["message"]
    query_logs.assert_not_called()
    assert captured["calls"] == 0


async def test_empty_logs_are_returned_as_tool_result(log_chat):
    auth, captured = log_chat
    events = await collect(auth, knowledge_base_ids=[])
    assert captured["calls"] == 2
    auth.redis.set.reset_mock()
    query = ChatQuerySchema(message="查询", request_id="empty", log_query={})
    (runtime_logs.LOG_DIR / "fastcore.log").write_text("", encoding="utf-8")
    events = [event async for event in service.ChatService(auth).chat_query(query, structured=True)]
    assert events[-1]["type"] == "done"
    assert next(event for event in events if event["type"] == "log_result")["returned_count"] == 0
    assert '"entries": []' in captured["prompt"]
    assert captured["calls"] == 4


@pytest.mark.parametrize("context", [{"knowledge_base_ids": [1]}, {"files": [{"content": "unrelated"}]}])
async def test_log_analysis_rejects_mixed_context(log_chat, context):
    auth, captured = log_chat
    events = await collect(auth, **context)
    assert events[-1]["type"] == "error"
    assert "不能混用" in events[-1]["message"]
    assert captured["calls"] == 1


async def test_revocation_during_model_call_suppresses_answer(log_chat, monkeypatch):
    auth, captured = log_chat

    class Model:
        async def request_tools(self, messages, tools):
            if isinstance(messages[-1], ToolMessage):
                auth.db.scalar.return_value = None
                return AIMessage(content="must-not-disclose-answer")
            return AIMessage(content="", tool_calls=[{"name": "query_runtime_logs", "args": {}, "id": "log-1"}])

    monkeypatch.setattr(rag, "get_cached_chat_model", Model)
    events = await collect(auth)
    assert events[-1]["type"] == "error"
    assert "权限已失效" in events[-1]["message"]
    assert not any(event["type"] in {"chunk", "done"} for event in events)


async def test_model_error_is_not_success_and_never_exposes_provider_detail(log_chat, monkeypatch):
    auth, captured = log_chat

    class Model:
        async def request_tools(self, messages, tools):
            raise RuntimeError("provider-url-with-secret")

    monkeypatch.setattr(rag, "get_cached_chat_model", Model)
    events = await collect(auth)
    assert events[-1]["type"] == "error"
    assert "provider-url-with-secret" not in str(events)
    assert not any(event["type"] == "done" for event in events)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_sdk_timeout_has_timeout_code_and_safe_fallback(log_chat, monkeypatch, provider, explicit):
    from anthropic import APITimeoutError as AnthropicTimeout
    from openai import APITimeoutError as OpenAITimeout

    auth, _ = log_chat
    error = (AnthropicTimeout if provider == "anthropic" else OpenAITimeout)(
        request=httpx.Request("POST", "https://fixture.invalid/synthetic-secret")
    )
    planner = AsyncMock(side_effect=error)
    reader = AsyncMock()
    monkeypatch.setattr(rag, "get_cached_chat_model", lambda: SimpleNamespace(request_tools=planner))
    monkeypatch.setattr(service, "query_runtime_logs", reader)
    query = ChatQuerySchema(message="普通问题", **({"log_query": {}} if explicit else {}))
    events = [event async for event in service.ChatService(auth)._stream_log_analysis(query)]
    assert len(events) == 1
    assert events[0]["type"] == ("error" if explicit else "warning")
    assert events[0]["error_code"] == "log_analysis_timeout"
    assert "synthetic-secret" not in str(events)
    if not explicit:
        assert "未读取运行日志" in events[0]["message"]
        assert "不保存到会话" in events[0]["message"]
    planner.assert_awaited_once()
    reader.assert_not_called()


async def test_log_analysis_cancellation_propagates(log_chat, monkeypatch):
    auth, captured = log_chat

    class Model:
        async def request_tools(self, messages, tools):
            raise asyncio.CancelledError

    monkeypatch.setattr(rag, "get_cached_chat_model", Model)
    with pytest.raises(asyncio.CancelledError):
        await collect(auth)


async def test_builtin_tool_works_without_log_mode(log_chat):
    auth, captured = log_chat
    events = [event async for event in service.ChatService(auth).chat_query(ChatQuerySchema(message="查询后台错误日志"), structured=True)]
    assert events[-1]["type"] == "done"
    assert len(events[-1]["log_context_id"]) == 32
    assert captured["calls"] == 2
    assert next(event for event in events if event["type"] == "tool_call")["name"] == "query_runtime_logs"


@pytest.mark.parametrize(
    "call",
    [
        {"name": "run_shell", "args": {}, "id": "unknown"},
        {"name": "query_runtime_logs", "args": {"path": "/etc/passwd"}, "id": "path"},
        {"name": "query_runtime_logs", "args": {"minutes": 0}, "id": "range"},
        {"name": "query_runtime_logs", "args": {"minutes": True}, "id": "bool"},
    ],
)
async def test_invalid_tool_calls_never_read_logs(log_chat, monkeypatch, call):
    auth, captured = log_chat
    reader = AsyncMock()
    monkeypatch.setattr(service, "query_runtime_logs", reader)
    model = SimpleNamespace(request_tools=AsyncMock(return_value=AIMessage(content="", tool_calls=[call])))
    monkeypatch.setattr(rag, "get_cached_chat_model", lambda: model)
    events = await collect(auth)
    assert events[-1]["type"] == "error"
    reader.assert_not_called()


async def test_continued_queries_have_unique_citation_ids(log_chat, monkeypatch):
    auth, _ = log_chat
    count = 0

    class Model:
        async def request_tools(self, messages, tools):
            nonlocal count
            count += 1
            if count == 5:
                return AIMessage(content="分析 [1] [4]")
            return AIMessage(content="", tool_calls=[{"name": "query_runtime_logs", "args": {}, "id": f"log-{count}"}])

    monkeypatch.setattr(rag, "get_cached_chat_model", Model)
    events = await collect(auth)
    assert count == 5
    assert events[-1]["type"] == "done"
    latest_citations = [event for event in events if event["type"] == "citations"][-1]["citations"]
    assert [item["id"] for item in latest_citations] == ["1", "2", "3", "4"]


async def test_no_tool_call_keeps_regular_chat_path(log_chat, monkeypatch):
    auth, captured = log_chat
    model = SimpleNamespace(request_tools=AsyncMock(return_value=AIMessage(content="route only")))
    monkeypatch.setattr(rag, "get_cached_chat_model", lambda: model)
    reader = AsyncMock()
    monkeypatch.setattr(service, "query_runtime_logs", reader)
    assert [event async for event in service.ChatService(auth)._stream_log_analysis(ChatQuerySchema(message="普通问题"))] == []
    reader.assert_not_called()


async def test_ordinary_chat_does_not_receive_tools(log_chat, monkeypatch):
    auth, captured = log_chat
    auth.user.is_superuser = False
    planner = AsyncMock(side_effect=AssertionError("Ordinary users must not receive tools"))
    monkeypatch.setattr(rag, "get_cached_chat_model", lambda: SimpleNamespace(request_tools=planner))
    monkeypatch.setattr(service.ChatService, "_should_persist_session", lambda self: False)

    class Chain:
        async def astream(self, **kwargs):
            yield "ordinary answer"

    monkeypatch.setattr(service, "create_rag_chain", lambda **kwargs: Chain())
    events = [event async for event in service.ChatService(auth).chat_query(ChatQuerySchema(message="普通问题"), structured=True)]
    assert events[-1]["type"] == "done"
    planner.assert_not_called()


@pytest.mark.parametrize("scenario", ["duplicate-batch-id", "repeat-id", "invalid-json", "revoked-before-evidence"])
async def test_tool_boundary_failures_do_not_disclose_answer(log_chat, monkeypatch, scenario):
    auth, captured = log_chat
    call = {"name": "query_runtime_logs", "args": {}, "id": "same-call"}
    reader = AsyncMock(return_value={"entries": [], "notice": "empty"})
    monkeypatch.setattr(service, "query_runtime_logs", reader)
    if scenario == "revoked-before-evidence":

        async def revoke(*args):
            auth.db.scalar.return_value = None
            return {"entries": [{"message": "private-evidence"}]}

        reader.side_effect = revoke
    if scenario == "invalid-json":
        answer = AIMessage(content="", invalid_tool_calls=[{"name": "query_runtime_logs", "args": "invalid-json", "id": "invalid", "error": "parse error"}])
    else:
        answer = AIMessage(content="", tool_calls=[call, call] if scenario == "duplicate-batch-id" else [call])
    monkeypatch.setattr(rag, "get_cached_chat_model", lambda: SimpleNamespace(request_tools=AsyncMock(return_value=answer)))
    events = await collect(auth)
    assert events[-1]["type"] == "error"
    assert not any(event["type"] in {"chunk", "done"} for event in events)
    assert "private-evidence" not in json.dumps(events)
    assert reader.await_count == (1 if scenario in {"repeat-id", "revoked-before-evidence"} else 0)


async def test_non_stream_chat_uses_builtin_tool(log_chat):
    auth, captured = log_chat
    result = await service.ChatService(auth).chat_non_stream("查询错误日志", None)
    assert result["function_calls"][0]["name"] == "query_runtime_logs"
    assert result["session_id"] == ""
    assert "split-secret-value" not in result["response"]


@pytest.mark.parametrize("protocol", ["openai", "openai_responses", "anthropic"])
async def test_real_provider_adapter_serializes_tool_schema_and_tool_result(monkeypatch, protocol, usage_storage):
    from anthropic import AsyncAnthropic
    from langchain_openai import ChatOpenAI

    from app.plugin.module_ai.chat import model_config_service

    requests = []
    http = httpx2 if protocol == "anthropic" else httpx

    def transport(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 2:
            if protocol == "anthropic":
                result = {
                    "id": "msg-final",
                    "type": "message",
                    "role": "assistant",
                    "model": "fixture",
                    "content": [{"type": "text", "text": "分析 [1]"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            elif protocol == "openai_responses":
                result = {
                    "id": "resp-final",
                    "object": "response",
                    "status": "completed",
                    "created_at": 0,
                    "model": "fixture",
                    "output": [{"type": "message", "id": "msg-final", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": "分析 [1]", "annotations": []}]}],
                }
            else:
                result = {
                    "id": "chat-final",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "fixture",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "分析 [1]"}, "finish_reason": "stop"}],
                }
        elif protocol == "anthropic":
            result = {
                "id": "msg-plan",
                "type": "message",
                "role": "assistant",
                "model": "fixture",
                "content": [{"type": "tool_use", "id": "call-log", "name": "query_runtime_logs", "input": {"minutes": 30}}],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        elif protocol == "openai_responses":
            result = {
                "id": "resp-plan",
                "object": "response",
                "status": "completed",
                "created_at": 0,
                "model": "fixture",
                "output": [{"type": "function_call", "id": "fc-log", "call_id": "call-log", "name": "query_runtime_logs", "arguments": '{"minutes":30}', "status": "completed"}],
            }
        else:
            result = {
                "id": "chat-plan",
                "object": "chat.completion",
                "created": 0,
                "model": "fixture",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{"id": "call-log", "type": "function", "function": {"name": "query_runtime_logs", "arguments": '{"minutes":30}'}}],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        return http.Response(200, json=result)

    async with http.AsyncClient(transport=http.MockTransport(transport)) as client:
        monkeypatch.setattr(
            model_config_service, "_active_config", model_config_service.ChatModelRuntimeConfig(protocol=protocol, api_key="synthetic-fixture", base_url="https://fixture.invalid", model="fixture")
        )
        monkeypatch.setattr(rag, "ChatOpenAI", lambda **kwargs: ChatOpenAI(http_async_client=client, **kwargs))
        model = rag.LangChainChatModel()
        if protocol == "anthropic":
            model.llm._async_client = AsyncAnthropic(api_key="synthetic-fixture", base_url="https://fixture.invalid", http_client=client, max_retries=0)
        messages = [SystemMessage(content="日志助手"), HumanMessage(content="查询错误日志")]
        answer = await model.request_tools(messages, [runtime_logs.RUNTIME_LOG_TOOL])
        assert answer.tool_calls[0]["name"] == "query_runtime_logs"
        assert answer.tool_calls[0]["args"] == {"minutes": 30}
        assert "query_runtime_logs" in json.dumps(requests[0]["tools"])
        messages.extend([answer, ToolMessage(content="REDACTED-EVIDENCE-42", tool_call_id="call-log")])
        final = await model.request_tools(messages, [runtime_logs.RUNTIME_LOG_TOOL])
        assert rag.content_to_text(final.content) == "分析 [1]"
        assert not final.tool_calls
        serialized = json.dumps(requests[1])
        assert "REDACTED-EVIDENCE-42" in serialized and "call-log" in serialized
        expected_type = {"openai": '"role": "tool"', "openai_responses": '"type": "function_call_output"', "anthropic": '"type": "tool_result"'}[protocol]
        assert expected_type in serialized


def test_log_request_rejects_arbitrary_paths_and_invalid_ranges():
    for payload in [{"path": "/etc/passwd"}, {"minutes": 0}, {"levels": ["SHELL"]}, {"correlation_id": "../outside"}]:
        with pytest.raises(ValidationError):
            ChatQuerySchema(message="分析", log_query=payload)


def test_log_status_requires_login_and_is_enabled_for_superuser(ai_client, auth_headers):
    response = ai_client.get("/ai/chat/runtime-logs/status")
    assert response.status_code == 401
    response = ai_client.get("/ai/chat/runtime-logs/status", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["data"] == {"can_query": True, "enabled": True}
