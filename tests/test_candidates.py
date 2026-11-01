from datetime import date

from next_app.candidates import select_candidates, task_date
from next_app.models import Task


TODAY = date(2026, 10, 25)


def task(id, **kwargs):
    return Task(id=str(id), name="Task " + str(id), **kwargs)


def test_deadlines_priorities_and_long_term_survive_a_large_urgent_backlog():
    urgent = [task("u" + str(i), due="2026-10-26", project_id="school") for i in range(40)]
    overdue = task("overdue", deadline="2026-10-24")
    important = task("research", project_id="research", project_name="Research")
    high_priority = [task("p" + str(i), priority=4, project_id="career") for i in range(12)]
    result = select_candidates(urgent + [overdue, important] + high_priority, ["research"], TODAY)
    ids = [item.id for item in result]
    assert len(ids) == len(set(ids)) == 18
    assert ids[0] == "overdue"
    assert "research" in ids
    assert any(id.startswith("p") for id in ids)


def test_undated_tasks_have_space_even_without_priority_or_favorites():
    tasks = [task(i, due="2026-10-26", project_id="school") for i in range(30)]
    tasks += [task("creative", project_id="writing")]
    assert "creative" in {item.id for item in select_candidates(tasks, [], TODAY)}


def test_long_term_bucket_spreads_across_projects():
    tasks = [task("a" + str(i), project_id="a", priority=4) for i in range(25)]
    tasks += [task("b", project_id="b"), task("c", project_id="c")]
    ids = [item.id for item in select_candidates(tasks, [], TODAY)]
    assert "b" in ids[:12] and "c" in ids[:12]


def test_hard_deadline_is_not_hidden_by_later_due_date():
    item = task("exam", due="2026-11-03", deadline="2026-10-26")
    assert task_date(item) == date(2026, 10, 26)


def test_small_empty_and_excluded_lists():
    assert select_candidates([], [], TODAY) == []
    tasks = [task("a"), task("b"), task("c")]
    assert len(select_candidates(tasks, [], TODAY)) == 3
    assert [item.id for item in select_candidates(tasks, [], TODAY, {"a", "c"})] == ["b"]


def test_ai_payload_is_bounded_and_priority_is_mapped_correctly():
    item = task("a", priority=4, description="x" * 4000, labels=["y" * 100] * 30)
    data = item.ai_data()
    assert data["priority"] == "P1"
    assert len(data["description"]) == 700
    assert len(data["labels"]) == 8
    assert len(data["labels"][0]) == 60
