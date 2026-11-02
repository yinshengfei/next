"""Todoist task reads and reviewed writes."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, List

import httpx

from .models import Task


BASE_URL = "https://api.todoist.com/api/v1/"


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class TodoistWriteError(ServiceError):
    def __init__(self, message: str, uncertain: bool = False, status_code: int = 502):
        super().__init__(message, status_code)
        self.uncertain = uncertain


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


async def fetch_projects(token: str) -> List[dict]:
    async with httpx.AsyncClient(
        base_url=BASE_URL, headers={"Authorization": "Bearer " + token},
        timeout=20, follow_redirects=False,
    ) as client:
        projects = await read_pages(client, "projects")
    return [
        {"id": str(item["id"]), "name": str(item.get("name", ""))[:150],
         "is_inbox_project": bool(item.get("inbox_project"))}
        for item in projects if "id" in item
    ]


async def fetch_completed_range(token: str, since: datetime, until: datetime) -> List[dict]:
    """Completed tasks in a bounded range of at most 90 days, every page."""
    if until <= since or until - since > timedelta(days=90):
        raise ValueError("Completed task range must be at most 90 days.")
    items = []
    cursor = None
    seen = set()
    async with httpx.AsyncClient(base_url=BASE_URL, headers={"Authorization": "Bearer " + token},
                                 timeout=20, follow_redirects=False) as client:
        for _ in range(100):
            params = {"since": since.isoformat(), "until": until.isoformat(), "limit": 200}
            if cursor:
                params["cursor"] = cursor
            try:
                response = await client.get("tasks/completed/by_completion_date", params=params)
            except (httpx.TimeoutException, httpx.HTTPError):
                raise ServiceError("Todoist could not load recently completed tasks. Please try again.") from None
            if response.status_code in (401, 403):
                raise ServiceError("Todoist rejected the token. Run setup to replace it.", 503)
            if response.status_code == 429:
                raise ServiceError("Todoist is rate-limiting requests. Try again later.", 429)
            if response.is_error or response.is_redirect:
                raise ServiceError("Todoist could not load recently completed tasks. Please try again.")
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ServiceError("Todoist returned an unexpected completed-task response.")
            items.extend(data["items"])
            cursor = data.get("next_cursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen:
                raise ServiceError("Todoist completed-task pagination did not finish safely.")
            seen.add(cursor)
        else:
            raise ServiceError("Too many completed-task pages to load safely.")
    result = []
    for item in items:
        if not isinstance(item, dict) or not item.get("id") or not item.get("completed_at"):
            raise ServiceError("Todoist returned an unexpected completed-task format.")
        result.append({"id": str(item["id"]), "name": str(item.get("content", "")),
                       "description": str(item.get("description") or ""),
                       "project_id": str(item.get("project_id") or ""),
                       "completed_at": str(item["completed_at"])})
    return result


async def fetch_recent_completed(token: str, now: datetime = None) -> List[dict]:
    now = now or datetime.now(timezone.utc)
    return await fetch_completed_range(token, now - timedelta(days=7), now)


async def fetch_account_started(token: str) -> datetime:
    """Read only the history boundary; never retain the user response (it can contain tokens)."""
    async with httpx.AsyncClient(base_url=BASE_URL, headers={"Authorization": "Bearer " + token},
                                 timeout=20, follow_redirects=False) as client:
        try:
            response = await client.get("user")
            response.raise_for_status()
            joined = datetime.fromisoformat(response.json()["joined_at"].replace("Z", "+00:00"))
            if joined.tzinfo is None:
                raise ValueError
            return joined
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise ServiceError("Could not determine the start of your Todoist history. Please try again.") from None


async def create_task(token: str, payload: dict) -> str:
    """Create one reviewed task; ambiguous failures must not be retried automatically."""
    async with httpx.AsyncClient(
        base_url=BASE_URL, headers={"Authorization": "Bearer " + token},
        timeout=20, follow_redirects=False,
    ) as client:
        try:
            response = await client.post("tasks", json=payload)
        except (httpx.TimeoutException, httpx.HTTPError):
            raise TodoistWriteError(
                "Could not confirm whether Todoist created this task. Check Todoist before trying again.",
                uncertain=True,
            ) from None
    if response.status_code in (401, 403):
        raise TodoistWriteError("Todoist rejected the token. Run setup to replace it.", status_code=503)
    if response.status_code == 429:
        raise TodoistWriteError("Todoist is rate-limiting requests. Try again later.", status_code=429)
    if response.is_error or response.is_redirect:
        raise TodoistWriteError("Todoist did not create this task. Check the fields and try again.")
    try:
        task_id = response.json()["id"]
        if not isinstance(task_id, str) or not task_id:
            raise ValueError
        return task_id
    except (ValueError, TypeError, KeyError):
        raise TodoistWriteError(
            "Todoist responded, but the created task could not be confirmed. Check Todoist before trying again.",
            uncertain=True,
        ) from None


async def write_existing_task(token: str, task_id: str, payload: dict = None, complete: bool = False):
    """Update or close a reviewed active task; ambiguous failures require a manual check."""
    if not task_id or not isinstance(task_id, str) or "/" in task_id:
        raise ServiceError("Invalid task ID.", 422)
    if not payload and not complete:
        return
    path = "tasks/" + task_id + ("/close" if complete else "")
    async with httpx.AsyncClient(base_url=BASE_URL, headers={"Authorization": "Bearer " + token},
                                 timeout=20, follow_redirects=False) as client:
        try:
            response = await client.post(path) if complete else await client.post(path, json=payload)
        except (httpx.TimeoutException, httpx.HTTPError):
            raise TodoistWriteError("Todoist may have applied this change. Check Todoist before retrying.",
                                    uncertain=True) from None
    if response.status_code in (401, 403):
        raise TodoistWriteError("Todoist rejected the token. Run setup to replace it.", status_code=503)
    if response.status_code == 429:
        raise TodoistWriteError("Todoist is rate-limiting requests. Try again later.", status_code=429)
    if response.is_error or response.is_redirect:
        raise TodoistWriteError("Todoist did not confirm this task change. Check the task and try again.")
