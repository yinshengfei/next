import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from next_app import ai, app as app_module
from next_app.config import Settings
from next_app.models import OverviewLayout, Task
from next_app.overview import build_overview
from next_app.todoist import ServiceError


HEADERS = {"X-Next-Local": "1"}


def test_code_owns_all_membership_and_group_progress():
    today = date.today()
    active = [
        {"id": "a", "name": "Write draft", "description": "Half done", "project_id": "p",
         "project_name": "Research", "due": today.isoformat(), "deadline": None},
        {"id": "b", "name": "Check figures", "description": "", "project_id": "p",
         "project_name": "Research", "due": (today + timedelta(days=20)).isoformat(), "deadline": None},
        {"id": "c", "name": "Undated", "description": "", "project_id": "q",
         "project_name": "Personal", "due": None, "deadline": None},
    ]
    completed = [{"id": "d", "name": "Outline", "description": "", "project_id": "p",
                  "completed_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}]
    layout = {key: {"area_id": "research", "area_title": "Research", "area_order": 0,
                    "stream_id": "paper", "stream_title": "Paper", "stream_order": 0,
                    "order": i, "emphasis": "focus", "attention": None}
              for i, key in enumerate(("open:a", "open:b", "done:d"))}
    result = build_overview(active, completed, layout, today=today, coverage_since="2025-01-01")
    sections = {section["key"]: section for section in result["sections"]}
    assert sections["today"]["count"] == 1
    assert sections["week"]["count"] == 1
    assert sections["all"]["count"] == 3
    assert sections["finished"]["count"] == 1
    paper = next(group for area in sections["all"]["areas"] for group in area["workstreams"] if group["key"] == "paper")
    assert paper["progress"] == {"open": 2, "finished": 1}
    assert {task["id"] for area in sections["all"]["areas"] for group in area["workstreams"] for task in group["tasks"]} == {"a", "b", "c"}


def test_overview_uses_cached_snapshot_and_layout(monkeypatch, tmp_path):
    counts = {"tasks": 0, "completed": 0, "layout": 0}
    async def tasks(token):
        counts["tasks"] += 1
        return [Task(id="a", name="Write draft", due=date.today().isoformat(), project_id="p")]
    async def projects(token):
        return [{"id": "p", "name": "Research"}]
    async def completed(token, since, until):
        counts["completed"] += 1
        return []
    async def account_started(token):
        return datetime.now(timezone.utc) - timedelta(days=365)
    async def layout(settings, key, records, previous_layout=None, planning_context=""):
        counts["layout"] += 1
        return [{"task_id": "open:a", "area_id": "research", "area_title": "Research", "area_order": 0,
                 "stream_id": "paper", "stream_title": "Paper", "stream_order": 0,
                 "order": 0, "emphasis": "focus", "attention": "Due today"}]
    monkeypatch.setattr(app_module, "get_secret", lambda account: "secret")
    monkeypatch.setattr(app_module, "fetch_tasks", tasks)
    monkeypatch.setattr(app_module, "fetch_projects", projects)
    monkeypatch.setattr(app_module, "fetch_completed_range", completed)
    monkeypatch.setattr(app_module, "fetch_account_started", account_started)
    monkeypatch.setattr(app_module.ai, "layout_overview", layout)
    with TestClient(app_module.create_app(Settings(), overview_path=tmp_path / "overview.db"),
                    base_url="http://127.0.0.1:8765") as client:
        first = client.post("/api/overview", headers=HEADERS, json={}).json()
        second = client.post("/api/overview", headers=HEADERS, json={}).json()
        cached = client.get("/api/overview/cached").json()
    assert first["active_count"] == second["active_count"] == 1
    assert cached["available"] and cached["active_count"] == 1
    assert counts["tasks"] == 1 and counts["layout"] == 1 and counts["completed"] >= 4
    assert first["sections"][2]["areas"][0]["workstreams"][0]["title"] == "Paper"


def test_ai_layout_rejects_missing_or_duplicate_ids(monkeypatch):
    async def bad(*args, **kwargs):
        if len(args) >= 4 and args[3].__name__ == "OverviewTaxonomy":
            from next_app.models import OverviewTaxonomy
            return OverviewTaxonomy.model_validate({"areas": [{"area_id": "x", "title": "X", "order": 0,
                "workstreams": [{"stream_id": "s", "title": "S", "order": 0}]}]})
        return OverviewLayout.model_validate({"items": [{
            "task_id": "open:a", "stream_id": "s", "order": 1, "emphasis": "standard",
        }]})
    monkeypatch.setattr(ai, "structured_call", bad)
    with pytest.raises(ServiceError, match="omitted"):
        asyncio.run(ai.layout_overview(Settings(), "key", [
            {"task_id": "open:a", "title": "A", "finished": False},
            {"task_id": "open:b", "title": "B", "finished": False},
        ]))


@pytest.mark.parametrize("failure", ["missing", "duplicate", "unknown_stream"])
def test_layout_repair_preserves_good_items_and_injects_only_unresolved_records(monkeypatch, failure):
    from next_app.models import OverviewTaxonomy
    calls = []
    def placement(task_id, **changes):
        return {"task_id": task_id, "stream_id": "paper", "order": 2,
                "emphasis": "standard", **changes}
    async def respond(settings, key, payload, schema, instruction, **kwargs):
        if schema.__name__ == "OverviewAudit":
            return schema()
        if schema is OverviewTaxonomy:
            return OverviewTaxonomy.model_validate({"areas": [{"area_id": "research", "title": "Research", "order": 0,
                "workstreams": [{"stream_id": "paper", "title": "Paper", "order": 0}]}]})
        calls.append(payload)
        if len(calls) == 1:
            items = [placement("open:a", order=17, attention="Keep this valid note")]
            if failure == "duplicate":
                items.extend([placement("done:b"), placement("done:b", order=9)])
            if failure == "unknown_stream":
                items.append(placement("done:b", stream_id="invented"))
            return OverviewLayout.model_validate({"items": items})
        assert payload["required_task_ids"] == ["T2"]
        assert [item["task_id"] for item in payload["records"]] == ["T2"]
        assert payload["accepted_placements"][0]["task_id"] == "T1"
        assert payload["validation_feedback"]
        return OverviewLayout.model_validate({"items": [placement("done:b")]})
    monkeypatch.setattr(ai, "structured_call", respond)
    result = asyncio.run(ai.layout_overview(Settings(), "key", [
        {"task_id": "open:a", "title": "Draft", "finished": False},
        {"task_id": "done:b", "title": "Outline", "finished": True},
    ]))
    assert len(calls) == 2
    assert [item["task_id"] for item in result] == ["open:a", "done:b"]
    assert result[0]["order"] == 17 and result[0]["attention"] == "Keep this valid note"


def test_incremental_layout_reuses_unchanged_membership_and_audits_specific_moves(monkeypatch):
    from next_app.models import OverviewAudit, OverviewTaxonomy
    placement_inputs = []
    async def respond(settings, key, payload, schema, instruction, **kwargs):
        if schema is OverviewTaxonomy:
            return schema.model_validate({"areas": [{"area_id": "research", "title": "Research", "order": 0,
                "workstreams": [{"stream_id": "stats", "title": "Statistics", "order": 0},
                                {"stream_id": "dta", "title": "DTA", "order": 1}]}]})
        if schema is OverviewAudit:
            return schema.model_validate({"moves": [{"task_id": "open:b", "stream_id": "dta",
                "reason": "DTA submission belongs with DTA, not statistics"}]})
        placement_inputs.append([item["task_id"] for item in payload["records"]])
        return OverviewLayout.model_validate({"items": [{"task_id": item["task_id"], "stream_id": "stats",
            "order": 2, "emphasis": "standard"} for item in payload["records"]]})
    monkeypatch.setattr(ai, "structured_call", respond)
    records = [{"task_id": "open:a", "title": "Statistics draft", "finished": False},
               {"task_id": "open:b", "title": "DTA submission", "finished": False}]
    first = asyncio.run(ai.layout_overview(Settings(), "key", records))
    assert next(item for item in first if item["task_id"] == "open:b")["stream_id"] == "dta"
    records[1]["description"] = "Draft ready"
    second = asyncio.run(ai.layout_overview(Settings(), "key", records,
        previous_layout={item["task_id"]: item for item in first}))
    assert placement_inputs == [["T1", "T2"], ["T2"]]
    assert next(item for item in second if item["task_id"] == "open:a")["stream_id"] == "stats"
