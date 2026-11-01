import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from next_app import ai, app as app_module, todoist
from next_app.config import Settings
from next_app.models import AIBreakdown, AIRecommendations, Preferences, Task
from next_app.todoist import ServiceError


def run(awaitable):
    return asyncio.run(awaitable)


def action(id, minutes=10):
    return {
        "task_id": id, "action": "Draft three bullets", "estimated_minutes": minutes,
        "why": "A useful start on the supplied task.", "first_step": "Open your notes.",
        "assumptions": ["Assumes you have existing notes."],
    }


def api_response(payload, status="completed"):
    return {
        "id": "resp_test", "object": "response", "created_at": 1, "status": status,
        "model": "gpt-5.4-mini", "output": [{
            "id": "msg_test", "type": "message", "status": "completed", "role": "assistant",
            "content": [{"type": "output_text", "text": json.dumps(payload), "annotations": []}],
        }],
    }


@pytest.fixture
def upstream(monkeypatch):
    """Exercise real HTTP clients and SDK parsing, with no credentials or network."""
    requests = []
    state = {"ai_status": 200, "todoist_status": 200, "payload": None}

    def handle(request):
        requests.append(request)
        if request.url.host == "api.todoist.com":
            assert request.method == "GET", "Todoist must remain read-only"
            assert request.headers["authorization"] == "Bearer test-todoist-token"
            if state["todoist_status"] != 200:
                return httpx.Response(state["todoist_status"], json={"error": "private upstream message"})
            if request.url.path.endswith("/projects"):
                return httpx.Response(200, json={"results": [{"id": "p", "name": "Research", "is_favorite": True}], "next_cursor": None})
            if request.url.params.get("cursor") == "page2":
                return httpx.Response(200, json={"results": [{"id": "3", "content": "Academic CV", "project_id": "p", "parent_id": "1"}], "next_cursor": None})
            return httpx.Response(200, json={"results": [
                {"id": "1", "content": "Research plan", "project_id": "p", "priority": 4, "due": {"date": "2026-10-26"}, "deadline": {"date": "2026-10-28"}},
                {"id": "2", "content": "Read paper", "project_id": "p", "duration": {"amount": 1, "unit": "day"}},
            ], "next_cursor": "page2"})
        assert request.url.host == state.get("ai_host", "api.openai.com")
        if state.get("ai_timeout"):
            raise httpx.ReadTimeout("private timeout detail", request=request)
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
        if state["ai_status"] != 200:
            return httpx.Response(state["ai_status"], json={"error": {"message": "private upstream message", "type": "test"}})
        if "raw_ai_response" in state:
            return httpx.Response(200, json=state["raw_ai_response"])
        payload = state["payload"]
        if payload is None:
            if body["text"]["format"]["name"] == "AIBreakdown":
                payload = {"steps": [{"action": "Open your notes.", "estimated_minutes": 2}], "assumptions": []}
            else:
                candidates = json.loads(body["input"])["tasks"]
                payload = {"recommendations": [action(task["task_id"]) for task in candidates[:3]]}
        return httpx.Response(200, json=api_response(payload))

    original = httpx.AsyncClient

    class Client(original):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handle)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(app_module, "get_secret", lambda account: "test-todoist-token" if account == "todoist" else "test-openai-key")
    return requests, state


def test_todoist_pagination_project_parent_and_duration(upstream):
    requests, _ = upstream
    tasks = run(todoist.fetch_tasks("test-todoist-token"))
    assert len(tasks) == 3
    assert tasks[0].priority == 4 and tasks[0].deadline == "2026-10-28"
    assert tasks[1].duration_minutes == 1440
    assert tasks[2].parent_name == "Research plan"
    assert tasks[2].project_name == "Research"
    assert len(requests) == 3


def test_ai_timeout_releases_lock_and_keeps_local_app_usable(upstream):
    requests, state = upstream
    state["ai_timeout"] = True
    headers = {"X-Next-Local": "1"}
    with TestClient(app_module.create_app(Settings()), base_url="http://localhost") as client:
        response = client.post("/api/recommend", headers=headers, json={"minutes": 30, "energy": "low"})
        assert response.status_code == 504
        assert "Next is still running" in response.json()["detail"]
        assert "private timeout detail" not in response.text
        assert client.get("/").status_code == 200
        state["ai_timeout"] = False
        response = client.post("/api/recommend", headers=headers, json={"minutes": 30, "energy": "low"})
        assert response.status_code == 200
    assert sum(request.url.host == "api.openai.com" for request in requests) == 2


def test_complete_read_only_recommendation_and_easier_flow(upstream):
    requests, _ = upstream
    with TestClient(app_module.create_app(Settings()), base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/status").json()["configured"] is True
        headers = {"X-Next-Local": "1", "Origin": "http://127.0.0.1:8765"}
        response = client.post("/api/recommend", headers=headers, json={"minutes": 30, "energy": "low"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["candidate_count"] == data["total_tasks"] == 3
        assert {card["task_name"] for card in data["recommendations"]} == {"Research plan", "Read paper", "Academic CV"}
        card = data["recommendations"][0]
        response = client.post("/api/easier", headers=headers, json={
            "minutes": 15, "energy": "low", "recommendation_id": card["recommendation_id"],
        })
        assert response.status_code == 200, response.text
        assert response.json()["steps"][0]["estimated_minutes"] == 2
        assert client.post("/api/complete", headers=headers, json={"task_id": "1"}).status_code == 404
        assert client.post("/api/easier", headers=headers, json={
            "minutes": 15, "energy": "low", "recommendation_id": "made-up",
        }).status_code == 409
    assert all(request.method == "GET" for request in requests if request.url.host == "api.todoist.com")


@pytest.mark.parametrize("payload", [
    {"recommendations": [action("invented")]},
    {"recommendations": [action("1", 31)]},
    {"recommendations": [action("1"), action("1")]},
])
def test_unusable_ai_recommendations_are_rejected(upstream, payload):
    _, state = upstream
    state["payload"] = payload
    tasks = [Task(id="1", name="Real task")]
    with pytest.raises(ServiceError):
        run(ai.recommend(Settings(), "test-key", tasks, Preferences(minutes=30, energy="low")))


def test_breakdown_total_must_fit_time(upstream):
    _, state = upstream
    state["payload"] = {"steps": [
        {"action": "Step one", "estimated_minutes": 10},
        {"action": "Step two", "estimated_minutes": 10},
    ], "assumptions": []}
    with pytest.raises(ServiceError, match="exceeded"):
        run(ai.make_easier(Settings(), "test-key", Task(id="1", name="Real task"),
                           AIRecommendations(recommendations=[action("1")]).recommendations[0],
                           Preferences(minutes=15, energy="low")))


@pytest.mark.parametrize("service,status,expected", [
    ("todoist", 401, 503), ("todoist", 429, 429), ("todoist", 500, 502),
    ("ai", 401, 503), ("ai", 429, 429), ("ai", 500, 502), ("ai", 400, 502),
])
def test_upstream_errors_are_safe_and_do_not_retry(upstream, service, status, expected):
    requests, state = upstream
    state[service + "_status"] = status
    with TestClient(app_module.create_app(Settings()), base_url="http://localhost") as client:
        response = client.post("/api/recommend", headers={"X-Next-Local": "1"}, json={"minutes": 30, "energy": "medium"})
        assert response.status_code == expected
        assert "private upstream message" not in response.text
        assert "test-openai-key" not in response.text
    if service == "ai":
        assert sum(request.url.host == "api.openai.com" for request in requests) == 1


def test_empty_todoist_does_not_call_ai(upstream, monkeypatch):
    requests, _ = upstream

    async def empty(token):
        return []

    monkeypatch.setattr(app_module, "fetch_tasks", empty)
    with TestClient(app_module.create_app(Settings()), base_url="http://localhost") as client:
        response = client.post("/api/recommend", headers={"X-Next-Local": "1"}, json={"minutes": 15, "energy": "low"})
        assert response.status_code == 200
        assert response.json()["recommendations"] == []
    assert not requests


def test_only_shortlist_goes_to_ai_and_two_task_lists_work(upstream, monkeypatch):
    requests, _ = upstream

    async def many(token):
        return [Task(id=str(i), name="Task " + str(i), project_name="Research") for i in range(100)]

    monkeypatch.setattr(app_module, "fetch_tasks", many)
    with TestClient(app_module.create_app(Settings(important_projects=["Research"])), base_url="http://localhost") as client:
        response = client.post("/api/recommend", headers={"X-Next-Local": "1"}, json={"minutes": 30, "energy": "medium"})
        assert response.status_code == 200
        assert response.json()["candidate_count"] == 18
        assert response.json()["total_tasks"] == 100
    payload = json.loads(next(request.content for request in requests if request.url.host == "api.openai.com"))
    candidates = json.loads(payload["input"])["tasks"]
    assert len(candidates) == 18
    assert all(task["important_project"] for task in candidates)
    assert "test-openai-key" not in payload["input"]

    async def two(token):
        return [Task(id="a", name="A"), Task(id="b", name="B")]

    monkeypatch.setattr(app_module, "fetch_tasks", two)
    with TestClient(app_module.create_app(Settings()), base_url="http://localhost") as client:
        response = client.post("/api/recommend", headers={"X-Next-Local": "1"}, json={"minutes": 30, "energy": "medium"})
        assert response.status_code == 200
        assert len(response.json()["recommendations"]) == 2
        response = client.post("/api/recommend", headers={"X-Next-Local": "1"}, json={"minutes": 30, "energy": "medium", "exclude_ids": ["a", "b"]})
        assert response.status_code == 200 and not response.json()["recommendations"]


def test_pagination_loop_is_not_silently_truncated():
    async def load():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [], "next_cursor": "same"}))
        async with httpx.AsyncClient(base_url="https://api.todoist.com/api/v1/", transport=transport) as client:
            return await todoist.read_pages(client, "tasks")
    with pytest.raises(ServiceError, match="pagination"):
        run(load())


def test_transport_timeout_is_readable():
    def timeout(request):
        raise httpx.ReadTimeout("private transport detail", request=request)

    async def load():
        async with httpx.AsyncClient(base_url="https://api.todoist.com/api/v1/", transport=httpx.MockTransport(timeout)) as client:
            await todoist.read_pages(client, "tasks")
    with pytest.raises(ServiceError, match="too long"):
        run(load())
