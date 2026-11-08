"""
module_platform API route tests (Menu management).
"""

import pytest
from conftest import assert_route
from fastapi.testclient import TestClient


@pytest.fixture
def menu_id(test_client: TestClient, auth_headers: dict):
    response = test_client.post(
        "/platform/menu/create",
        headers=auth_headers,
        json={"name": "test menu", "type": 1, "order": 1, "route_name": "ApiTestMenu", "route_path": "/api-test-menu", "redirect": "/api-test-menu/child"},
    )
    assert response.status_code == 200
    identifier = response.json()["data"]["id"]
    yield identifier
    assert_route(test_client, "DELETE", "/platform/menu/delete", auth=auth_headers, json=[identifier])


class TestMenu:
    """Menu management routes."""

    def test_menu_tree(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/menu/tree", auth=auth_headers)

    def test_menu_create(self, test_client: TestClient, auth_headers: dict, menu_id: int) -> None:
        response = test_client.get(f"/platform/menu/detail/{menu_id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["data"]["name"] == "test menu"

    def test_menu_update(self, test_client: TestClient, auth_headers: dict, menu_id: int) -> None:
        assert_route(test_client, "PUT", f"/platform/menu/update/{menu_id}", auth=auth_headers, json={"name": "updated menu"})
        response = test_client.get(f"/platform/menu/detail/{menu_id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["data"]["name"] == "updated menu"

    def test_menu_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/platform/menu/delete", auth=auth_headers, json=[9999])

    def test_menu_detail(self, test_client: TestClient, auth_headers: dict, menu_id: int) -> None:
        assert_route(test_client, "GET", f"/platform/menu/detail/{menu_id}", auth=auth_headers)

    def test_menu_status_batch(self, test_client: TestClient, auth_headers: dict, menu_id: int) -> None:
        assert_route(
            test_client,
            "PATCH",
            "/platform/menu/status/batch",
            auth=auth_headers,
            json={"ids": [menu_id], "status": 1},
        )
