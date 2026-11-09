from types import SimpleNamespace

import pytest

from app.core.exceptions import CustomException
from app.plugin.module_ai.chat import model_config_service
from app.plugin.module_ai.chat.schema import AiModelConfigUpdateSchema, AiModelListRequestSchema


class _Result:
    def __init__(self, record) -> None:
        self.record = record

    def scalars(self):
        return self

    def first(self):
        return self.record


class _FakeDb:
    def __init__(self) -> None:
        self.record = None

    async def execute(self, _statement):
        return _Result(self.record)

    def add(self, record) -> None:
        self.record = record

    async def flush(self) -> None:
        return None


async def test_update_model_config_encrypts_key_and_keeps_vector_settings_read_only(monkeypatch) -> None:
    import socket

    monkeypatch.setattr(
        "app.plugin.module_ai.config.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    monkeypatch.setattr(model_config_service, "_active_config", None)
    monkeypatch.setattr(model_config_service.settings, "OPENAI_API_KEY", "env-key")
    monkeypatch.setattr(model_config_service.settings, "OPENAI_BASE_URL", "https://embedding.example/v1")
    monkeypatch.setattr(model_config_service.settings, "OPENAI_MODEL", "env-chat")
    monkeypatch.setattr(model_config_service.settings, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(model_config_service.settings, "LOCAL_EMBEDDING_MODEL", "bge-small")

    db = _FakeDb()
    auth = SimpleNamespace(db=db, user=SimpleNamespace(id=7))
    result = await model_config_service.update_model_config(
        auth,
        AiModelConfigUpdateSchema(
            chat_protocol="anthropic",
            openai_base_url="https://api.anthropic.com",
            openai_model="claude-sonnet-4-6",
            openai_api_key="claude-secret",
        ),
    )

    assert result.chat_protocol == "anthropic"
    assert result.openai_base_url == "https://api.anthropic.com"
    assert result.openai_model == "claude-sonnet-4-6"
    assert result.openai_api_key_configured is True
    assert result.embedding_provider == "local"
    assert result.local_embedding_model == "bge-small"
    assert db.record is not None
    assert db.record.protocol == "anthropic"
    assert db.record.encrypted_api_key != "claude-secret"
    assert "claude-secret" not in db.record.encrypted_api_key


@pytest.mark.parametrize(
    ("protocol", "saved_protocol"),
    [("openai", "openai"), ("openai_responses", "openai"), ("openai", "openai_responses")],
)
async def test_list_openai_models_reuses_saved_key_for_same_endpoint(monkeypatch, protocol: str, saved_protocol: str) -> None:
    monkeypatch.setattr(model_config_service.settings, "MODEL_ALLOWED_HOSTS", ["api.example.test"])

    async def load_config(_auth):
        return model_config_service.ChatModelRuntimeConfig(saved_protocol, "https://api.example.test/v1", "old", "saved-key")

    monkeypatch.setattr(model_config_service, "load_runtime_chat_model_config", load_config)
    requests = []

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            pass

        async def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return SimpleNamespace(status_code=200, json=lambda: {"data": [{"id": "model-b"}, {"id": "model-a"}]})

    monkeypatch.setattr(model_config_service.httpx, "AsyncClient", FakeClient)
    result = await model_config_service.list_provider_models(
        SimpleNamespace(),
        AiModelListRequestSchema(chat_protocol=protocol, openai_base_url="https://api.example.test/v1"),
    )
    assert result == ["model-a", "model-b"]
    assert requests == [("https://api.example.test/v1/models", {"headers": {"Authorization": "Bearer saved-key"}, "params": None})]


async def test_list_models_requires_new_key_for_changed_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(model_config_service.settings, "MODEL_ALLOWED_HOSTS", ["api.example.test", "other.example.test"])

    async def load_config(_auth):
        return model_config_service.ChatModelRuntimeConfig("openai", "https://api.example.test/v1", "old", "saved-key")

    monkeypatch.setattr(model_config_service, "load_runtime_chat_model_config", load_config)
    with pytest.raises(CustomException, match="API Key"):
        await model_config_service.list_provider_models(
            SimpleNamespace(),
            AiModelListRequestSchema(chat_protocol="openai", openai_base_url="https://other.example.test/v1"),
        )


async def test_list_models_uses_entered_key_for_changed_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(model_config_service.settings, "MODEL_ALLOWED_HOSTS", ["api.example.test", "other.example.test"])

    async def load_config(_auth):
        return model_config_service.ChatModelRuntimeConfig("openai", "https://api.example.test/v1", "old", "saved-key")

    monkeypatch.setattr(model_config_service, "load_runtime_chat_model_config", load_config)
    requests = []

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            pass

        async def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return SimpleNamespace(status_code=200, json=lambda: {"data": [{"id": "new-model"}]})

    monkeypatch.setattr(model_config_service.httpx, "AsyncClient", FakeClient)
    result = await model_config_service.list_provider_models(
        SimpleNamespace(),
        AiModelListRequestSchema(chat_protocol="openai_responses", openai_base_url="https://other.example.test/v1", openai_api_key="new-key"),
    )
    assert result == ["new-model"]
    assert requests[0][1]["headers"] == {"Authorization": "Bearer new-key"}


async def test_save_responses_protocol_reuses_same_endpoint_key_with_intercepted_dns(monkeypatch) -> None:
    import socket

    monkeypatch.setattr(model_config_service, "_active_config", None)
    monkeypatch.setattr(model_config_service.settings, "MODEL_ALLOWED_HOSTS", [])
    monkeypatch.setattr(model_config_service.settings, "OPENAI_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setattr(model_config_service.settings, "OPENAI_API_KEY", "saved-key")
    monkeypatch.setattr(
        "app.plugin.module_ai.config.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.125", 443))],
    )

    db = _FakeDb()
    auth = SimpleNamespace(db=db, user=SimpleNamespace(id=7))
    result = await model_config_service.update_model_config(
        auth,
        AiModelConfigUpdateSchema(chat_protocol="openai_responses", openai_base_url="https://api.example.test/v1", openai_model="response-model"),
    )
    assert result.chat_protocol == "openai_responses"
    assert result.openai_model == "response-model"
    assert db.record.encrypted_api_key != "saved-key"


async def test_save_changed_endpoint_requires_fresh_key(monkeypatch) -> None:
    monkeypatch.setattr(model_config_service, "_active_config", None)
    monkeypatch.setattr(model_config_service.settings, "MODEL_ALLOWED_HOSTS", ["api.example.test", "other.example.test"])
    monkeypatch.setattr(model_config_service.settings, "OPENAI_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setattr(model_config_service.settings, "OPENAI_API_KEY", "saved-key")

    db = _FakeDb()
    with pytest.raises(CustomException, match="API Key"):
        await model_config_service.update_model_config(
            SimpleNamespace(db=db, user=SimpleNamespace(id=7)),
            AiModelConfigUpdateSchema(chat_protocol="openai", openai_base_url="https://other.example.test/v1", openai_model="other-model"),
        )
    assert db.record is None


async def test_list_models_accepts_configured_endpoint_with_local_dns_interception(monkeypatch) -> None:
    import socket

    monkeypatch.setattr(model_config_service.settings, "MODEL_ALLOWED_HOSTS", [])
    monkeypatch.setattr(
        "app.plugin.module_ai.config.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.125", 443))],
    )

    async def load_config(_auth):
        return model_config_service.ChatModelRuntimeConfig("openai", "https://api.example.test/v1", "old", "saved-key")

    monkeypatch.setattr(model_config_service, "load_runtime_chat_model_config", load_config)

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            pass

        async def get(self, _url, **_kwargs):
            return SimpleNamespace(status_code=200, json=lambda: {"data": [{"id": "model-a"}]})

    monkeypatch.setattr(model_config_service.httpx, "AsyncClient", FakeClient)
    assert await model_config_service.list_provider_models(
        SimpleNamespace(),
        AiModelListRequestSchema(chat_protocol="openai", openai_base_url="https://api.example.test/v1"),
    ) == ["model-a"]

    with pytest.raises(ValueError, match="内网"):
        await model_config_service.list_provider_models(
            SimpleNamespace(),
            AiModelListRequestSchema(chat_protocol="openai", openai_base_url="https://other.example.test/v1", openai_api_key="new-key"),
        )


async def test_list_anthropic_models_follows_pages(monkeypatch) -> None:
    monkeypatch.setattr(model_config_service.settings, "MODEL_ALLOWED_HOSTS", ["api.anthropic.test"])

    async def load_config(_auth):
        return model_config_service.ChatModelRuntimeConfig("anthropic", "https://api.anthropic.test", "old", "saved-key")

    monkeypatch.setattr(model_config_service, "load_runtime_chat_model_config", load_config)
    requests = []

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            pass

        async def get(self, url, **kwargs):
            requests.append((url, kwargs))
            payload = (
                {"data": [{"id": "claude-b"}], "has_more": True, "last_id": "claude-b"}
                if len(requests) == 1 else {"data": [{"id": "claude-a"}], "has_more": False}
            )
            return SimpleNamespace(status_code=200, json=lambda: payload)

    monkeypatch.setattr(model_config_service.httpx, "AsyncClient", FakeClient)
    result = await model_config_service.list_provider_models(
        SimpleNamespace(),
        AiModelListRequestSchema(chat_protocol="anthropic", openai_base_url="https://api.anthropic.test"),
    )
    assert result == ["claude-a", "claude-b"]
    assert requests[0][0] == "https://api.anthropic.test/v1/models"
    assert requests[0][1]["headers"] == {"x-api-key": "saved-key", "anthropic-version": "2023-06-01"}
    assert requests[1][1]["params"]["after_id"] == "claude-b"


def test_ai_model_config_mapper_resolves_user_model():
    from sqlalchemy.orm import class_mapper

    from app.plugin.module_ai.chat.model import AiModelConfigModel

    mapper = class_mapper(AiModelConfigModel)
    assert mapper is not None
    assert "created_by" in mapper.relationships
    assert mapper.relationships["created_by"].mapper.class_.__name__ == "UserModel"


