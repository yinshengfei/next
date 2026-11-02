import asyncio
from copy import deepcopy

import pytest

from next_app.config import Settings
from next_app.models import OverviewPatch
from next_app.overview_patch import record_hash, update_layout
from next_app.todoist import ServiceError


def placement(record, stream="paper"):
    return dict(task_id=record["task_id"], stream_id=stream, stream_title=stream,
                stream_order=0, area_id="research", area_title="Research", area_order=0,
                order=4, emphasis="standard", attention=None,
                source_hash=record_hash(record), source_record=deepcopy(record))


def test_diff_completion_removal_addition_and_frozen_unrelated():
    a = dict(task_id="open:a", title="Paper", finished=False)
    b = dict(task_id="open:b", title="Other", finished=False)
    c = dict(task_id="open:c", title="Deleted", finished=False)
    prior = {r["task_id"]: placement(r, r["title"]) for r in [a, b, c]}
    old = deepcopy(prior)
    done = dict(task_id="done:a", title="Paper", finished=True, completed_at="2026-11-02")
    new = dict(task_id="open:d", title="Paper figures", finished=False)
    async def call(settings, key, payload, schema, instruction, **kwargs):
        assert schema is OverviewPatch
        assert {c["kind"] for c in payload["changes"]} == {"completed", "added"}
        assert payload["removed"][0]["task_id"] == "open:c"
        assert "open:b" not in payload["editable_task_ids"]
        return schema(items=[dict(task_id=t, stream_id="Paper", order=4, emphasis="standard")
                             for t in payload["required_task_ids"]])
    result = asyncio.run(update_layout(Settings(), "key", [done, b, new], prior, call))
    by_id = {p["task_id"]: p for p in result}
    assert set(by_id) == {"done:a", "open:b", "open:d"}
    assert by_id["open:b"] == prior["open:b"]
    assert by_id["done:a"]["stream_id"] == "Paper"
    assert prior == old


def test_patch_repairs_only_missing_preserves_accepted():
    old = dict(task_id="open:a", title="Paper", finished=False)
    a = {**old, "description": "Updated"}
    b = dict(task_id="open:b", title="Figures", finished=False)
    calls = []
    async def call(settings, key, payload, schema, instruction, **kwargs):
        calls.append(payload)
        task_id = "open:a" if len(calls) == 1 else "open:b"
        return schema(items=[dict(task_id=task_id, stream_id="paper", order=9, emphasis="quiet")])
    result = asyncio.run(update_layout(Settings(), "key", [a,b], {"open:a": placement(old)}, call))
    assert len(calls) == 2
    assert calls[1]["required_task_ids"] == ["open:b"]
    assert calls[1]["accepted_edits"][0]["order"] == 9
    assert result[0]["order"] == 9


def test_patch_rejects_changes_to_unaffected_task_and_keeps_input():
    a = dict(task_id="open:a", title="Paper", finished=False)
    b = dict(task_id="open:b", title="Other", finished=False)
    previous = {"open:a": placement(a), "open:b": placement(b, "other")}
    copy = deepcopy(previous)
    async def call(settings, key, payload, schema, instruction, **kwargs):
        return schema(items=[dict(task_id="open:b", stream_id="paper", order=1, emphasis="focus")])
    with pytest.raises(ServiceError):
        asyncio.run(update_layout(Settings(), "key", [{**a, "description": "Changed"}, b], previous, call))
    assert previous == copy


def test_no_changes_makes_no_model_call():
    a = dict(task_id="open:a", title="Paper", finished=False)
    async def call(*args, **kwargs):
        pytest.fail("No diff must not call AI")
    previous = {"open:a": placement(a)}
    assert asyncio.run(update_layout(Settings(), "key", [a], previous, call)) == list(previous.values())
