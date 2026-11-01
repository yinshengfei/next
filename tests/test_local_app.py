import json
import stat

import pytest
from fastapi.testclient import TestClient

from next_app import app as app_module
from next_app.config import Settings, SetupError, load_settings, save_settings


@pytest.fixture
def client():
    with TestClient(app_module.create_app(Settings()), base_url="http://127.0.0.1:8765") as client:
        yield client


def test_static_page_is_self_contained_and_cannot_serve_source(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Find My Next Action" in response.text
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/../config.py").status_code == 404
    assert client.get("/config.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_external_hosts_and_cross_origin_posts_are_blocked(client):
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    body = {"minutes": 30, "energy": "medium"}
    assert client.post("/api/recommend", json=body).status_code == 403
    assert client.post("/api/recommend", headers={"X-Next-Local": "1", "Origin": "https://evil.example"}, json=body).status_code == 403
    assert client.post("/api/recommend", headers={"X-Next-Local": "1", "Content-Type": "text/plain"}, content=json.dumps(body)).status_code == 403


@pytest.mark.parametrize("body", [
    {"minutes": 0, "energy": "low"}, {"minutes": 481, "energy": "low"},
    {"minutes": 15.5, "energy": "low"}, {"minutes": True, "energy": "low"},
    {"minutes": 30, "energy": "unknown"}, {"minutes": 30, "energy": "low", "api_key": "should-not-echo"},
])
def test_invalid_input_is_rejected_before_any_api_call(client, body):
    response = client.post("/api/recommend", headers={"X-Next-Local": "1"}, json=body)
    assert response.status_code == 422
    assert "should-not-echo" not in response.text


def test_missing_credentials_produce_setup_guidance(client, monkeypatch):
    def missing(account):
        raise SetupError("Credentials are missing. Run: python -m next_app setup")
    monkeypatch.setattr(app_module, "get_secret", missing)
    assert client.get("/api/status").json()["configured"] is False
    response = client.post("/api/recommend", headers={"X-Next-Local": "1"}, json={"minutes": 30, "energy": "low"})
    assert response.status_code == 503 and "setup" in response.json()["detail"]


def test_settings_permissions_and_validation(tmp_path):
    path = tmp_path / "Next" / "config.json"
    assert load_settings(path).model == "gpt-5.4-mini"
    save_settings(Settings(important_projects=["Research"], language="简体中文"), path)
    assert load_settings(path).language == "简体中文"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    path.write_text('{"port": "broken"}')
    with pytest.raises(SetupError):
        load_settings(path)
