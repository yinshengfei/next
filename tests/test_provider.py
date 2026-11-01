import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from next_app import __main__ as cli, ai, app as app_module
from next_app.config import (
    KEYCHAIN_SERVICE, OPENAI_BASE_URL, Settings, SetupError, get_secret, load_settings, save_settings,
)
from next_app.models import AIRecommendations, Preferences, Task
from next_app.todoist import ServiceError
from tests.test_integrations import action, run, upstream


CREDITS_URL = "https://api.openai-next.com/v1"
CREDITS_MODEL = "gpt-6.1-sol"  # Publicly listed example; account access is not assumed.


def test_old_config_and_official_key_account_still_work(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"provider": "openai", "model": "gpt-5.4-mini"}')
    settings = load_settings(path)
    assert settings.base_url == OPENAI_BASE_URL
    assert settings.ai_key_account == "openai"


def test_custom_endpoint_credentials_are_isolated_and_trailing_slashes_normalized():
    first = Settings(base_url=CREDITS_URL + "/")
    assert first.base_url == CREDITS_URL
    assert first.ai_key_account == "ai:" + CREDITS_URL
    assert first.ai_key_account != Settings().ai_key_account
    assert first.ai_key_account != Settings(base_url="https://another.example/v1").ai_key_account
    assert first.ai_key_account != Settings(base_url=CREDITS_URL + "/different-prefix").ai_key_account
    assert Settings(base_url="https://API.OPENAI.COM:443/v1/").ai_key_account == "openai"


@pytest.mark.parametrize("url", [
    "http://api.openai-next.com/v1", "file:///tmp/key", "https://",
    "https://key@api.openai-next.com/v1", "https://user:key@api.openai-next.com/v1",
    CREDITS_URL + "?api_key=do-not-store", CREDITS_URL + "#key",
    CREDITS_URL + "\n", CREDITS_URL + "/responses", CREDITS_URL + "/chat/completions/",
])
def test_unsafe_or_complete_endpoint_urls_rejected(url):
    with pytest.raises(ValidationError):
        Settings(base_url=url)


def test_model_and_endpoint_round_trip_without_secrets(tmp_path):
    path = tmp_path / "Next" / "config.json"
    save_settings(Settings(provider="openai-compatible", base_url=CREDITS_URL, model=CREDITS_MODEL), path)
    settings = load_settings(path)
    assert settings.base_url == CREDITS_URL and settings.model == CREDITS_MODEL
    assert "api_key" not in json.loads(path.read_text())
    with pytest.raises(ValidationError):
        Settings(model="   ")


def test_custom_endpoint_keeps_recommendations_and_easier_flow(upstream, monkeypatch):
    requests, state = upstream
    state["ai_host"] = "api.openai-next.com"
    settings = Settings(provider="openai-compatible", base_url=CREDITS_URL, model=CREDITS_MODEL)
    accounts = []

    def read_secret(account):
        accounts.append(account)
        return "test-todoist-token" if account == "todoist" else "test-credits-key"

    monkeypatch.setattr(app_module, "get_secret", read_secret)
    # A user's SDK environment must not override the endpoint saved in Next.
    monkeypatch.setenv("OPENAI_BASE_URL", "https://wrong.example/v1")
    with TestClient(app_module.create_app(settings), base_url="http://localhost") as client:
        assert client.get("/api/status").json()["configured"] is True
        headers = {"X-Next-Local": "1"}
        response = client.post("/api/recommend", headers=headers, json={"minutes": 30, "energy": "low"})
        assert response.status_code == 200, response.text
        card = response.json()["recommendations"][0]
        response = client.post("/api/easier", headers=headers, json={
            "minutes": 15, "energy": "low", "recommendation_id": card["recommendation_id"],
        })
        assert response.status_code == 200, response.text
        assert response.json()["steps"][0]["estimated_minutes"] == 2
    ai_requests = [request for request in requests if request.url.host == state["ai_host"]]
    assert len(ai_requests) == 2
    assert "openai" not in accounts
    assert settings.ai_key_account in accounts
    for request in ai_requests:
        assert str(request.url) == CREDITS_URL + "/responses"
        assert request.headers["authorization"] == "Bearer test-credits-key"
        body = json.loads(request.content)
        assert body["model"] == CREDITS_MODEL
        assert body["store"] is False
        assert body["max_output_tokens"] == 2200
        assert "reasoning" not in body
        assert "test-credits-key" not in body["input"]


def test_custom_endpoint_never_uses_an_existing_official_key(upstream, monkeypatch):
    requests, _ = upstream

    def only_official_and_todoist(account):
        if account == "todoist":
            return "test-todoist-token"
        if account == "openai":
            pytest.fail("A custom endpoint must not read the official OpenAI key")
        raise SetupError("Credentials are missing. Run: python -m next_app setup")

    monkeypatch.setattr(app_module, "get_secret", only_official_and_todoist)
    settings = Settings(base_url=CREDITS_URL)
    with TestClient(app_module.create_app(settings), base_url="http://localhost") as client:
        assert client.get("/api/status").json()["configured"] is False
        response = client.post("/api/recommend", headers={"X-Next-Local": "1"}, json={"minutes": 30, "energy": "low"})
        assert response.status_code == 503
    assert all(request.url.host == "api.todoist.com" for request in requests)


def test_setup_keeps_todoist_and_saves_key_to_custom_keychain_account(monkeypatch, tmp_path, capsys):
    settings_path = tmp_path / "Next" / "config.json"
    existing = Settings(language="简体中文", important_projects=["Research"])
    monkeypatch.setattr(cli, "load_settings", lambda: existing)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    values = iter(["", "test-credits-secret"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(values))
    saved_keys = []
    monkeypatch.setattr(cli, "set_secret", lambda account, value: saved_keys.append((account, value)))
    monkeypatch.setattr(cli, "save_settings", lambda settings: save_settings(settings, settings_path))
    cli.setup(CREDITS_URL, CREDITS_MODEL)
    assert saved_keys == [("ai:" + CREDITS_URL, "test-credits-secret")]
    settings = load_settings(settings_path)
    assert settings.language == "简体中文" and settings.important_projects == ["Research"]
    assert settings.provider == "openai-compatible" and settings.model == CREDITS_MODEL
    assert "test-credits-secret" not in settings_path.read_text()
    assert "test-credits-secret" not in capsys.readouterr().out


def test_provider_check_uses_real_request_format_and_only_synthetic_data(upstream, monkeypatch, capsys):
    requests, state = upstream
    state["ai_host"] = "api.openai-next.com"
    settings = Settings(base_url=CREDITS_URL, model=CREDITS_MODEL)
    accounts = []

    def secret(account):
        accounts.append(account)
        return "test-credits-key"

    monkeypatch.setattr(cli, "get_secret", secret)
    run(cli.check_provider(settings))
    assert accounts == [settings.ai_key_account]
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert json.loads(body["input"])["tasks"][0]["task_id"] == "next-connection-check"
    output = capsys.readouterr().out
    assert "Passed" in output and "test-credits-key" not in output


def test_provider_format_error_has_no_upstream_secrets(upstream):
    requests, state = upstream
    state.update(ai_host="api.openai-next.com", ai_status=400)
    with pytest.raises(ServiceError, match="Responses API") as error:
        run(ai.recommend(Settings(base_url=CREDITS_URL), "test-key", [Task(id="1", name="Real task")], Preferences(minutes=30, energy="low")))
    assert "private upstream message" not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize("raw_response", [
    {"choices": [{"message": {"content": "private upstream message"}}]},
    {"id": "resp_test", "status": "completed", "output": []},
])
def test_incompatible_response_format_is_rejected_safely(upstream, raw_response):
    requests, state = upstream
    state.update(ai_host="api.openai-next.com", raw_ai_response=raw_response)
    with pytest.raises(ServiceError) as error:
        run(ai.recommend(Settings(base_url=CREDITS_URL), "test-key", [Task(id="1", name="Real task")], Preferences(minutes=30, energy="low")))
    assert "private upstream message" not in str(error.value)
    assert len(requests) == 1


def test_redirects_are_not_followed_with_credentials(monkeypatch):
    requests = []
    original = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://untrusted.example/responses"})

    class Client(original):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handle)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    with pytest.raises(ServiceError):
        run(ai.structured_call(Settings(base_url=CREDITS_URL), "test-key", {}, AIRecommendations, "Test"))
    assert len(requests) == 1 and requests[0].url.host == "api.openai-next.com"


def test_keychain_failure_does_not_fall_back(monkeypatch):
    from next_app import config
    calls = []

    class EmptyKeychain:
        def get_password(self, service, account):
            calls.append((service, account))
            return None

    monkeypatch.setattr(config, "keychain", lambda: EmptyKeychain())
    account = Settings(base_url=CREDITS_URL).ai_key_account
    with pytest.raises(SetupError):
        get_secret(account)
    assert calls == [(KEYCHAIN_SERVICE, account)]
