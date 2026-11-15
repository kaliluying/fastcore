"""
模块接口测试 —— module_ai（AI 对话模块）

动态路由映射：module_ai → /ai
每个接口一个测试用例，覆盖会话 CRUD 与 AI 对话。
"""

import pytest
from conftest import assert_route
from fastapi.testclient import TestClient


class TestKnowledgeStats:
    """首页知识库概览统计。"""

    def test_knowledge_stats(self, ai_client: TestClient, auth_headers: dict) -> None:
        response = ai_client.get("/ai/knowledge/stats", headers=auth_headers)

        assert response.status_code == 200
        stats = response.json()["data"]
        assert set(stats) == {
            "knowledge_bases",
            "documents",
            "indexed_documents",
            "processing_documents",
            "failed_documents",
        }
        assert all(isinstance(value, int) and value >= 0 for value in stats.values())
        assert (
            stats["indexed_documents"]
            + stats["processing_documents"]
            + stats["failed_documents"]
            <= stats["documents"]
        )

    def test_knowledge_stats_requires_auth(self, ai_client: TestClient) -> None:
        assert_route(ai_client, "GET", "/ai/knowledge/stats", expected_status=401)


class TestAiChat:
    """会话真实 CRUD，以及未经认证的 AI 请求边界。"""

    def test_session_crud(self, ai_client: TestClient, auth_headers: dict) -> None:
        response = ai_client.post("/ai/chat/create", headers=auth_headers, json={"title": "测试会话"})
        assert response.status_code == 200
        created = response.json()["data"]
        session_id = created["session_id"]
        assert created["runs"] == created["messages"] == []
        for field in ("agent_id", "team_id", "workflow_id", "agent_data", "team_data", "workflow_data", "metadata", "summary"):
            assert created[field] is None
        try:
            listing = ai_client.get("/ai/chat/list", headers=auth_headers)
            assert listing.status_code == 200
            listed = next(item for item in listing.json()["data"]["items"] if item["session_id"] == session_id)
            assert listed == created
            detail = ai_client.get(f"/ai/chat/detail/{session_id}", headers=auth_headers)
            assert detail.status_code == 200
            assert detail.json()["data"] == created
            assert_route(ai_client, "PUT", f"/ai/chat/update/{session_id}", auth=auth_headers, json={"title": "更新会话"})
            updated = ai_client.get(f"/ai/chat/detail/{session_id}", headers=auth_headers)
            assert updated.status_code == 200
            assert updated.json()["data"]["title"] == "更新会话"
        finally:
            assert_route(ai_client, "DELETE", "/ai/chat/delete", auth=auth_headers, json=[session_id])

    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("GET", "/ai/chat/list", None),
            ("GET", "/ai/chat/detail/test_session", None),
            ("POST", "/ai/chat/create", {"title": "测试会话"}),
            ("PUT", "/ai/chat/update/test_session", {"title": "更新会话"}),
            ("DELETE", "/ai/chat/delete", ["test_session"]),
            ("POST", "/ai/chat/ai-chat", {"message": "你好", "session_id": "test_session"}),
        ],
    )
    def test_requires_authentication(self, ai_client: TestClient, method: str, path: str, payload) -> None:
        assert_route(ai_client, method, path, expected_status=401, json=payload)
