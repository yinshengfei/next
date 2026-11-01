"""Three small buckets, not a priority score or another task system."""

from collections import defaultdict, deque
from datetime import date, timedelta
from typing import List, Optional, Set

from .models import Task


MAX_CANDIDATES = 18
BUCKET_SIZE = 6


def task_date(task: Task) -> date:
    dates = []
    for value in (task.deadline, task.due):
        if value:
            try:
                dates.append(date.fromisoformat(value[:10]))
            except ValueError:
                pass
    return min(dates, default=date.max)


def by_project(tasks: List[Task]) -> List[Task]:
    """Take one from each project before taking a second from any project."""
    projects = defaultdict(deque)
    for task in tasks:
        projects[task.project_id].append(task)
    result = []
    while any(projects.values()):
        for queue in projects.values():
            if queue:
                result.append(queue.popleft())
    return result


def select_candidates(
    tasks: List[Task],
    important_projects: List[str],
    today: Optional[date] = None,
    exclude_ids: Optional[Set[str]] = None,
) -> List[Task]:
    today = today or date.today()
    cutoff = today + timedelta(days=7)
    excluded = exclude_ids or set()
    remaining = [task for task in tasks if task.id not in excluded]
    important = {value.casefold() for value in important_projects}
    selected: List[Task] = []
    seen: Set[str] = set()

    def take(pool: List[Task], count: int) -> None:
        added = 0
        for task in pool:
            if task.id not in seen:
                selected.append(task)
                seen.add(task.id)
                added += 1
                if added >= count:
                    break

    urgent = sorted(
        (task for task in remaining if task_date(task) <= cutoff),
        key=lambda task: (task_date(task), -task.priority, task.id),
    )
    take(urgent, BUCKET_SIZE)

    # Keep a separate place for longer-term work, even with an urgent backlog.
    def important_task(task: Task) -> bool:
        return bool(
            task.priority >= 3 or task.favorite_project
            or task.project_id.casefold() in important
            or task.project_name.casefold() in important
            or {label.casefold() for label in task.labels} & {"long-term", "long_term", "important"}
        )

    long_term = [task for task in remaining if task_date(task) > cutoff]
    long_term.sort(key=lambda task: (not important_task(task), -task.priority, task_date(task), task.id))
    take(by_project(long_term), BUCKET_SIZE)

    high_priority = sorted(
        (task for task in remaining if task.priority >= 3),
        key=lambda task: (-task.priority, task_date(task), task.id),
    )
    take(by_project(high_priority), BUCKET_SIZE)

    # Fill unused slots across projects; undated ordinary tasks can still appear.
    fallback = sorted(remaining, key=lambda task: (-task.priority, task_date(task), task.id))
    if len(selected) < MAX_CANDIDATES:
        take(by_project(fallback), MAX_CANDIDATES - len(selected))
    return selected
