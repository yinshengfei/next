"""Todoist reads only. There is deliberately no write/complete method."""

import asyncio
from typing import Any, List

import httpx

from .models import Task


BASE_URL = "https://api.todoist.com/api/v1/"


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


async def read_pages(client: httpx.AsyncClient, resource: str) -> List[dict]:
    results: List[dict] = []
    cursor = None
    seen = set()
    for _ in range(100):
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            response = await client.get(resource, params=params)
        except httpx.TimeoutException:
            raise ServiceError("Todoist took too long to respond. Please try again.", 504) from None
        except httpx.HTTPError:
            raise ServiceError("Cannot reach Todoist. Check your internet connection.") from None
        if response.status_code in (401, 403):
            raise ServiceError("Todoist rejected the token. Run setup to replace it.", 503)
        if response.status_code == 429:
            raise ServiceError("Todoist is rate-limiting requests. Wait a little and try again.", 429)
        if response.is_error:
            raise ServiceError("Todoist could not load your tasks. Please try again.")
        try:
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError
            if not all(isinstance(item, dict) for item in data["results"]):
                raise ValueError
            results.extend(data["results"])
            cursor = data.get("next_cursor")
            if cursor is not None and not isinstance(cursor, str):
                raise ValueError
        except (ValueError, TypeError):
            raise ServiceError("Todoist returned an unexpected response. Please try again.") from None
        if not cursor:
            return results
        if cursor in seen:
            raise ServiceError("Todoist pagination did not finish. Please try again.")
        seen.add(cursor)
    raise ServiceError("Too many Todoist pages to load safely. No partial task list was used.")


def date_value(value: Any):
    return value.get("date") if isinstance(value, dict) else None


async def fetch_tasks(token: str) -> List[Task]:
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": "Bearer " + token},
        timeout=20,
        follow_redirects=False,
    ) as client:
        raw_tasks, raw_projects = await asyncio.gather(
            read_pages(client, "tasks"), read_pages(client, "projects"),
        )
    projects = {str(project["id"]): project for project in raw_projects if "id" in project}
    names = {str(task["id"]): str(task.get("content", "")) for task in raw_tasks if "id" in task}
    tasks = []
    try:
        for item in raw_tasks:
            if item.get("checked") or item.get("is_deleted") or item.get("completed_at"):
                continue
            project_id = str(item.get("project_id") or "")
            project = projects.get(project_id, {})
            duration = item.get("duration") or {}
            amount = duration.get("amount")
            duration_minutes = None
            if isinstance(amount, int) and amount > 0:
                duration_minutes = amount * (1440 if duration.get("unit") == "day" else 1)
            parent_id = str(item["parent_id"]) if item.get("parent_id") else None
            tasks.append(Task(
                id=str(item["id"]), name=item["content"],
                description=item.get("description") or "", project_id=project_id,
                project_name=project.get("name", ""), favorite_project=bool(project.get("is_favorite")),
                priority=max(1, min(4, int(item.get("priority", 1)))),
                due=date_value(item.get("due")), deadline=date_value(item.get("deadline")),
                labels=item.get("labels") or [], duration_minutes=duration_minutes,
                parent_id=parent_id, parent_name=names.get(parent_id),
            ))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ServiceError("Todoist returned an unexpected task format. Please try again.") from None
    return tasks
