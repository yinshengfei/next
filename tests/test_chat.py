import asyncio
import json
import stat

import pytest

from fastapi.testclient import TestClient

from next_app import app as app_module
from next_app import ai
from next_app.chat_store import ChatStore
from next_app.config import Settings
from next_app.models import AIChatReply, ChatRequest, ConfirmDraftsRequest, Task
from next_app.todoist import TodoistWriteError, create_task
from next_app.todoist import ServiceError


HEADERS = {"X-Next-Local": "1"}
PROJECTS = [{"id": "p1", "name": "Work"}]


@pytest.mark.parametrize("phase,unresolved,question", [("clarify", [], "什么时候做？"), ("answer", [], None), ("preview", ["目标不清楚"], None), ("preview", [], "哪一个事项？")])
def test_dialogue_gate_never_stores_operations_before_ready(monkeypatch, tmp_path, phase, unresolved, question):
    client, path = setup_client(monkeypatch, tmp_path, {"phase": phase, "unresolved": unresolved,
        "clarification_question": question, "reply": "先确认信息",
        "drafts": [{"content": "Should not be proposed"}],
        "actions": [{"operation": "complete", "task_id": "existing", "reason": "Not ready"}]})
    with client:
        response = client.post("/api/chat", headers=HEADERS, json={"message": "Plan my day"})
        assert response.status_code == 200
        history = client.get("/api/chat/history").json()
        assert history["drafts"] == [] and history["actions"] == []
        if question:
            assert question in history["messages"][-1]["content"]


def test_confirmed_undated_task_immediately_visible_in_cached_all(monkeypatch, tmp_path):
    client, path = setup_client(monkeypatch, tmp_path, {"reply": "Preview without a date", "drafts": [{"content": "Undated task"}]})
    async def create(token, payload):
        assert "due_date" not in payload
        return "new-undated"
    monkeypatch.setattr(app_module, "create_task", create)
    with client:
        client.post("/api/chat", headers=HEADERS, json={"message": "Plan my day"})
        draft = client.get("/api/chat/history").json()["drafts"][0]
        assert client.post("/api/chat/confirm", headers=HEADERS, json={"drafts": [{"id": draft["id"], "content": draft["content"]}]}).status_code == 200
        overview = client.get("/api/overview/cached").json()
        all_view = next(v for v in overview["sections"] if v["key"] == "all")
        ids = [t["id"] for a in all_view["areas"] for g in a["workstreams"] for t in g["tasks"]]
        assert ids == ["new-undated"]
        assert next(v for v in overview["sections"] if v["key"] == "today")["count"] == 0


def test_chat_time_preserves_old_utc_and_new_send_time(tmp_path):
    store = ChatStore(tmp_path / "chat.db")
    with store.connect() as db:
        db.execute("INSERT INTO messages(role, content, created_at) VALUES ('user', 'Old', '2026-11-02 18:30:00')")
    store.add_message("user", "New", created_at="2026-11-03T03:05:12+08:00")
    expected = ["2026-11-03T02:30:00+08:00", "2026-11-03T03:05:12+08:00"]
    assert [m["created_at"] for m in store.recent()] == expected
    assert [m["created_at"] for m in store.history()["messages"]] == expected


def test_cancel_chat_creation_leaves_todoist_unchanged(monkeypatch, tmp_path):
    client, path = setup_client(monkeypatch, tmp_path, {"reply": "Preview", "drafts": [{"content": "New task"}]})
    async def forbidden(*args, **kwargs):
        pytest.fail("Cancellation must not write to Todoist")
    monkeypatch.setattr(app_module, "create_task", forbidden)
    with client:
        client.post("/api/chat", headers=HEADERS, json={"message": "Plan my day"})
        draft = client.get("/api/chat/history").json()["drafts"][0]
        body = {"id": draft["id"], "decision": "cancel"}
        assert client.post("/api/chat/draft/cancel", headers=HEADERS, json=body).status_code == 200
        assert client.post("/api/chat/draft/cancel", headers=HEADERS, json=body).status_code == 409
        assert client.get("/api/chat/history").json()["drafts"][0]["state"] == "cancelled"


@pytest.mark.parametrize("operation,changes", [("complete", {}), ("update", {"description": "Half finished"})])
def test_chat_action_requires_confirmation_and_cannot_repeat(monkeypatch, tmp_path, operation, changes):
    client, path = setup_client(monkeypatch, tmp_path, {
        "reply": "Review this action", "actions": [{"operation": operation, "task_id": "existing", "reason": "User requested", **changes}],
    })
    with client:
        writes = []
        async def write(token, task_id, payload, complete=False):
            writes.append((task_id, payload, complete))
        monkeypatch.setattr(app_module, "write_existing_task", write)
        assert client.post("/api/chat", headers=HEADERS, json={"message": "Plan my day"}).status_code == 200
        assert writes == []
        action = client.get("/api/chat/history").json()["actions"][0]
        assert action["before"]["name"] == "Existing task"
        body = {"id": action["id"], "decision": "confirm"}
        assert client.post("/api/chat/action", headers=HEADERS, json=body).status_code == 200
        assert client.post("/api/chat/action", headers=HEADERS, json=body).status_code == 409
        assert writes == [("existing", changes, operation == "complete")]
        assert ChatStore(path).history()["actions"][0]["state"] == "applied"


@pytest.mark.parametrize("scenario", ["cancel", "stale", "uncertain"])
def test_chat_action_cancel_stale_and_uncertain(monkeypatch, tmp_path, scenario):
    client, path = setup_client(monkeypatch, tmp_path, {
        "reply": "Review completion", "actions": [{"operation": "complete", "task_id": "existing", "reason": "Done"}],
    })
    with client:
        client.post("/api/chat", headers=HEADERS, json={"message": "Plan my day"})
        action = client.get("/api/chat/history").json()["actions"][0]
        writes = []
        async def write(*args, **kwargs):
            writes.append(1)
            raise TodoistWriteError("Connection lost", uncertain=True)
        monkeypatch.setattr(app_module, "write_existing_task", write)
        if scenario == "stale":
            async def tasks(token):
                return [Task(id="existing", name="Changed elsewhere")]
            monkeypatch.setattr(app_module, "fetch_tasks", tasks)
        decision = "cancel" if scenario == "cancel" else "confirm"
        client.post("/api/chat/action", headers=HEADERS, json={"id": action["id"], "decision": decision})
        state = ChatStore(path).history()["actions"][0]["state"]
        assert state == {"cancel": "cancelled", "stale": "stale", "uncertain": "uncertain"}[scenario]
        assert writes == ([1] if scenario == "uncertain" else [])
        assert client.post("/api/chat/action", headers=HEADERS, json={"id": action["id"], "decision": "confirm"}).status_code == 409


def setup_client(monkeypatch, tmp_path, reply):
    monkeypatch.setattr(app_module, "get_secret", lambda account: "test-secret")

    async def tasks(token):
        return [Task(id="existing", name="Existing task", project_name="Work")]

    async def projects(token):
        return PROJECTS

    async def chat(settings, key, history, tasks, projects):
        assert history[-1]["role"] == "user"
        assert history[-1]["content"] == "Plan my day"
        assert history[-1]["created_at"].endswith("+08:00")
        assert tasks[0].name == "Existing task"
        return AIChatReply.model_validate(reply)

    monkeypatch.setattr(app_module, "fetch_tasks", tasks)
    monkeypatch.setattr(app_module, "fetch_projects", projects)
    monkeypatch.setattr(app_module.ai, "chat", chat)
    path = tmp_path / "Next" / "chat.sqlite3"
    return TestClient(app_module.create_app(Settings(), chat_path=path), base_url="http://127.0.0.1:8765"), path


def test_recommended_task_can_be_completed_once(monkeypatch, tmp_path):
    from next_app.models import AIRecommendations
    async def tasks(token):
        return [Task(id="t1", name="Write draft")]
    async def recommend(settings, key, candidates, body):
        return AIRecommendations.model_validate({"recommendations": [{
            "task_id": "t1", "action": "Open draft", "estimated_minutes": 10,
            "why": "Due soon", "first_step": "Open file", "assumptions": [],
        }]})
    writes = []
    async def write(token, task_id, payload=None, complete=False):
        writes.append((task_id, payload, complete))
    monkeypatch.setattr(app_module, "get_secret", lambda account: "secret")
    monkeypatch.setattr(app_module, "fetch_tasks", tasks)
    monkeypatch.setattr(app_module.ai, "recommend", recommend)
    monkeypatch.setattr(app_module, "write_existing_task", write)
    with TestClient(app_module.create_app(Settings(), chat_path=tmp_path / "chat.db"), base_url="http://127.0.0.1:8765") as client:
        card = client.post("/api/recommend", headers=HEADERS, json={"minutes": 30, "energy": "medium"}).json()["recommendations"][0]
        body = {"recommendation_id": card["recommendation_id"]}
        assert client.post("/api/recommend/complete", headers=HEADERS, json=body).json()["completed"]
        assert client.post("/api/recommend/complete", headers=HEADERS, json=body).status_code == 409
    assert writes == [("t1", None, True)]


def test_progress_needs_confirmation_and_updates_existing_task(monkeypatch, tmp_path):
    from next_app.models import AIRecommendations, ProgressPlan
    async def tasks(token):
        return [Task(id="t1", name="Write draft", description="Outline ready", project_id="p1")]
    async def recommend(settings, key, candidates, body):
        return AIRecommendations.model_validate({"recommendations": [{
            "task_id": "t1", "action": "Open draft", "estimated_minutes": 10,
            "why": "Useful", "first_step": "Open file", "assumptions": [],
        }]})
    async def progress(settings, key, task, text):
        assert text == "Half done; need a figure"
        return ProgressPlan(reply="Review changes", updated_description="Outline ready. Half done.",
                            new_tasks=[{"content": "Make figure", "priority": 1}])
    writes = []
    async def write(token, task_id, payload=None, complete=False):
        writes.append(("update", task_id, payload, complete))
    async def create(token, payload):
        writes.append(("create", payload))
        return "new-id"
    async def projects(token):
        return PROJECTS
    monkeypatch.setattr(app_module, "get_secret", lambda account: "secret")
    monkeypatch.setattr(app_module, "fetch_tasks", tasks)
    monkeypatch.setattr(app_module, "fetch_projects", projects)
    monkeypatch.setattr(app_module.ai, "recommend", recommend)
    monkeypatch.setattr(app_module.ai, "plan_progress", progress)
    monkeypatch.setattr(app_module, "write_existing_task", write)
    monkeypatch.setattr(app_module, "create_task", create)
    with TestClient(app_module.create_app(Settings(), chat_path=tmp_path / "chat.db"), base_url="http://127.0.0.1:8765") as client:
        card = client.post("/api/recommend", headers=HEADERS, json={"minutes": 30, "energy": "medium"}).json()["recommendations"][0]
        proposal = client.post("/api/recommend/progress", headers=HEADERS, json={
            "recommendation_id": card["recommendation_id"], "progress": "Half done; need a figure",
        }).json()
        assert not writes
        body = {"proposal_id": proposal["proposal_id"], "updated_description": proposal["updated_description"],
                "new_tasks": proposal["new_tasks"], "complete": False}
        result = client.post("/api/recommend/progress/confirm", headers=HEADERS, json=body)
        assert result.status_code == 200 and result.json()["created_task_ids"] == ["new-id"]
        assert client.post("/api/recommend/progress/confirm", headers=HEADERS, json=body).status_code == 409
    assert writes == [("update", "t1", {"description": "Outline ready. Half done."}, False),
                      ("create", {"content": "Make figure", "description": "", "priority": 1})]


def test_chat_is_persistent_and_does_not_write_before_confirmation(monkeypatch, tmp_path):
    client, path = setup_client(monkeypatch, tmp_path, {
        "reply": "Here are two tasks for review.",
        "drafts": [
            {"content": "Write outline", "project_id": "p1", "priority": 1},
            {"content": "Email colleague", "due_date": "2026-11-03"},
        ],
    })

    async def forbidden(token, payload):
        raise AssertionError("Chat must not write to Todoist")

    monkeypatch.setattr(app_module, "create_task", forbidden)
    with client:
        response = client.post("/api/chat", headers=HEADERS, json={"message": "Plan my day"})
        assert response.status_code == 200
        assert len(response.json()["drafts"]) == 2
        history = client.get("/api/chat/history").json()
        assert [item["role"] for item in history["messages"]] == ["user", "assistant"]
        assert all(item["state"] == "pending" for item in history["drafts"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    with TestClient(app_module.create_app(Settings(), chat_path=path), base_url="http://127.0.0.1:8765") as reopened:
        assert len(reopened.get("/api/chat/history").json()["messages"]) == 2


def test_confirm_uses_reviewed_fields_once_and_blocks_unknown_project(monkeypatch, tmp_path):
    client, _ = setup_client(monkeypatch, tmp_path, {
        "reply": "Review this task.", "drafts": [{"content": "Original title"}],
    })
    created = []

    async def write(token, payload):
        created.append(payload)
        return "created-id"

    monkeypatch.setattr(app_module, "create_task", write)
    with client:
        draft_id = client.post("/api/chat", headers=HEADERS, json={"message": "Plan my day"}).json()["drafts"][0]["id"]
        reviewed = {"id": draft_id, "content": "Edited title", "description": "Details", "project_id": "p1", "due_date": "2026-11-03", "priority": 1}
        invalid = client.post("/api/chat/confirm", headers=HEADERS, json={"drafts": [{**reviewed, "project_id": "unknown"}]})
        assert invalid.status_code == 422 and not created
        first = client.post("/api/chat/confirm", headers=HEADERS, json={"drafts": [reviewed]})
        assert first.json()["results"][0]["state"] == "created"
        assert created == [{"content": "Edited title", "description": "Details", "project_id": "p1", "due_date": "2026-11-03", "priority": 1}]
        again = client.post("/api/chat/confirm", headers=HEADERS, json={"drafts": [reviewed]})
        assert again.json()["results"][0]["state"] == "not_pending"
        assert len(created) == 1


def test_ambiguous_write_failure_disables_retry(monkeypatch, tmp_path):
    client, _ = setup_client(monkeypatch, tmp_path, {
        "reply": "Review this task.", "drafts": [{"content": "One task"}],
    })

    async def uncertain(token, payload):
        raise TodoistWriteError("Check Todoist before retrying.", uncertain=True)

    monkeypatch.setattr(app_module, "create_task", uncertain)
    with client:
        draft_id = client.post("/api/chat", headers=HEADERS, json={"message": "Plan my day"}).json()["drafts"][0]["id"]
        payload = {"id": draft_id, "content": "One task"}
        first = client.post("/api/chat/confirm", headers=HEADERS, json={"drafts": [payload]})
        assert first.json()["results"][0]["state"] == "uncertain"
        second = client.post("/api/chat/confirm", headers=HEADERS, json={"drafts": [payload]})
        assert second.json()["results"][0]["state"] == "not_pending"


def test_restart_marks_inflight_create_uncertain(tmp_path):
    path = tmp_path / "Next" / "chat.sqlite3"
    store = ChatStore(path)
    drafts = store.add_reply("Review it", [AIChatReply.model_validate({
        "reply": "Review it", "drafts": [{"content": "Call Alex"}],
    }).drafts[0]])
    draft_id = drafts[0]["id"]
    assert store.claim(draft_id, {"content": "Call Alex", "priority": 4})
    reopened = ChatStore(path)
    assert reopened.history()["drafts"][0]["state"] == "uncertain"
    assert not reopened.claim(draft_id, {"content": "Call Alex", "priority": 4})


def test_chat_post_requires_local_header_and_clear_is_explicit(monkeypatch, tmp_path):
    client, _ = setup_client(monkeypatch, tmp_path, {"reply": "Hello", "drafts": []})
    with client:
        assert client.post("/api/chat", json={"message": "Plan my day"}).status_code == 403
        assert client.post("/api/chat", headers=HEADERS, json={"message": "Plan my day"}).status_code == 200
        assert len(client.get("/api/chat/history").json()["messages"]) == 2
        assert client.post("/api/chat/clear", headers=HEADERS, json={}).json() == {"cleared": True}
        assert client.get("/api/chat/history").json()["messages"] == []


def test_todoist_create_uses_v1_json_and_handles_ambiguous_timeout(monkeypatch):
    import httpx
    from next_app import todoist

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "task-1"})

    original = httpx.AsyncClient

    def client_factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(**kwargs)

    monkeypatch.setattr(todoist.httpx, "AsyncClient", client_factory)
    task_id = asyncio.run(create_task("secret", {"content": "Buy milk", "priority": 1}))
    assert task_id == "task-1"
    assert requests[0].url.path == "/api/v1/tasks"
    assert json.loads(requests[0].content) == {"content": "Buy milk", "priority": 1}
    assert requests[0].headers["authorization"] == "Bearer secret"


def test_long_source_plan_and_multiple_drafts_fit_chat_contract():
    assert len(ChatRequest(message="计划" * 5000).message) == 10000
    drafts = [{"content": "Task {}".format(index)} for index in range(27)]
    assert len(AIChatReply(reply="Review", drafts=drafts).drafts) == 27
    confirmed = [{"id": "draft-{}".format(index), "content": "Task {}".format(index)} for index in range(27)]
    assert len(ConfirmDraftsRequest(drafts=confirmed).drafts) == 27


def test_explicit_complete_list_count_must_be_preserved(monkeypatch):
    async def incomplete(*args, **kwargs):
        assert args[2]["expected_draft_count"] == 27
        return AIChatReply(reply="Partial", drafts=[{"content": "Task {}".format(i)} for i in range(18)])

    monkeypatch.setattr(ai, "structured_call", incomplete)
    with pytest.raises(ServiceError, match="18 of 27"):
        asyncio.run(ai.chat(Settings(), "test-key", [{"role": "user", "content": "完整任务档案\n27 项\n..."}], [], []))
