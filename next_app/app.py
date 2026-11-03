import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import ai
from .candidates import select_candidates
from .chat_store import ChatStore
from .chat_time import chat_now
from .overview import build_overview
from .overview_store import OverviewStore
from .config import Settings, SetupError, get_secret, load_settings
from .models import ChatActionDecision, ChatRequest, ConfirmDraftsRequest, EasierRequest, OverviewRequest, OverviewTaskInput, OverviewTaskEdit, ProgressConfirm, ProgressRequest, RecommendationAction, RecommendationRequest, Task
from .todoist import ServiceError, TodoistWriteError, create_task, delete_task, move_task, fetch_account_started, fetch_completed_range, fetch_projects, fetch_recent_completed, fetch_tasks, write_existing_task


STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings = None, chat_path: Path = None, overview_path: Path = None) -> FastAPI:
    settings = settings or load_settings()
    if chat_path is None and overview_path is not None:
        chat_path = Path(overview_path).with_name("chat.sqlite3")
    store = None
    overview_cache = None

    def chat_store():
        nonlocal store
        if store is None:
            store = ChatStore(chat_path) if chat_path is not None else ChatStore()
        return store

    def overview_store():
        nonlocal overview_cache
        if overview_cache is None:
            cache_path = overview_path or (Path(chat_path).with_name("overview.sqlite3") if chat_path else None)
            overview_cache = OverviewStore(cache_path)
        return overview_cache

    @asynccontextmanager
    async def lifespan(app):
        # Python 3.9 binds locks at creation; create this inside the running loop.
        app.state.ai_lock = asyncio.Lock()
        app.state.overview_lock = asyncio.Lock()
        yield

    app = FastAPI(title="Next", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    # Recommendation snapshots expire independently of the persistent Overview cache.
    suggestions = {}
    progress_proposals = {}

    def suggested_task(recommendation_id):
        saved = suggestions.get(recommendation_id)
        if saved is None or time.monotonic() - saved[2] > 1800:
            raise ServiceError("This suggestion has expired. Find your next action again.", 409)
        return saved[0]

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            expected_origin = "http://" + request.headers.get("host", "")
            if (
                request.headers.get("x-next-local") != "1"
                or (origin is not None and origin != expected_origin)
                or request.headers.get("content-type", "").split(";")[0].strip() != "application/json"
            ):
                return JSONResponse({"detail": "Use the Next page on this Mac."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(SetupError)
    async def setup_error(request: Request, exc: SetupError):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def input_error(request: Request, exc: RequestValidationError):
        # Do not echo request data in errors or log upstream error bodies.
        return JSONResponse({"detail": "Please check the submitted fields and try again."}, status_code=422)

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    async def status():
        try:
            await asyncio.to_thread(get_secret, "todoist")
            await asyncio.to_thread(get_secret, settings.ai_key_account)
        except SetupError as exc:
            return {"configured": False, "message": str(exc)}
        return {"configured": True, "message": "Ready"}

    @app.get("/api/chat/history")
    async def chat_history():
        return chat_store().history()

    @app.get("/api/projects")
    async def projects():
        token = await asyncio.to_thread(get_secret, "todoist")
        return {"projects": await fetch_projects(token)}

    @app.post("/api/overview")
    async def overview(body: OverviewRequest):
        async with app.state.overview_lock:
            cache = overview_store()
            now = datetime.now(timezone.utc)
            last_sync = cache.meta("last_sync")
            stale = (cache.meta("history_complete") != "1" or cache.meta("dirty") == "1" or not last_sync
                     or (now - datetime.fromisoformat(last_sync)).total_seconds() >= 300)
            sync_error = None
            if body.refresh or stale:
                try:
                    token = await asyncio.to_thread(get_secret, "todoist")
                    tasks, projects = await asyncio.gather(fetch_tasks(token), fetch_projects(token))
                    first_history_sync = cache.meta("history_complete") != "1"
                    start = await fetch_account_started(token) if first_history_sync else (
                        datetime.fromisoformat(last_sync) - timedelta(days=7) if last_sync else now - timedelta(days=7))
                    completed = []
                    cursor = start
                    while cursor < now:
                        end = min(cursor + timedelta(days=89), now)
                        completed.extend(await fetch_completed_range(token, cursor, end))
                        cursor = end
                    project_names = {item["id"]: item["name"] for item in projects}
                    for item in completed:
                        item["project_name"] = project_names.get(item["project_id"], "")
                    cache.sync([task.model_dump() for task in tasks], completed)
                    if first_history_sync:
                        cache.set_meta("history_since", start.date().isoformat())
                        cache.set_meta("history_complete", "1")
                except ServiceError as exc:
                    sync_error = str(exc)
            active, completed, layout = cache.snapshot()
            if not active and not completed and sync_error:
                raise ServiceError(sync_error)
            records = [
                {"task_id": "open:" + item["id"], "title": item["name"][:250],
                 "description": item.get("description", "")[:500], "project": item.get("project_name", "")[:100],
                 "due": item.get("due"), "deadline": item.get("deadline"), "priority": item.get("priority"),
                 "finished": False}
                for item in active
            ] + [
                {"task_id": "done:" + item["id"], "title": item["name"][:250],
                 "description": item.get("description", "")[:500], "project": item.get("project_name", "")[:100],
                 "completed_at": item.get("completed_at"), "finished": True}
                for item in completed
            ]
            records.sort(key=lambda item: item["task_id"])
            user_context = "\n\n".join(message["content"] for message in chat_store().recent(40)
                                        if message["role"] == "user")
            signature = hashlib.sha256(json.dumps({"version": 6, "model": settings.overview_model or settings.model,
                                                   "records": records}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            layout_mode = "ai"
            layout_error = None
            if not records:
                cache.save_layout([], signature=signature)
                layout = {}
            elif signature != cache.meta("layout_signature") and not app.state.ai_lock.locked():
                try:
                    async with app.state.ai_lock:
                        key = await asyncio.to_thread(get_secret, settings.ai_key_account)
                        placements = await ai.layout_overview(settings, key, records, previous_layout=layout,
                                                             planning_context=user_context)
                    cache.save_layout(placements, signature=signature)
                    layout = {item["task_id"]: item for item in placements}
                except ServiceError as exc:
                    layout_mode = "fallback"
                    layout_error = str(exc)
            elif records and signature != cache.meta("layout_signature"):
                layout_mode = "fallback"
            result = build_overview(active, completed, layout, coverage_since=cache.meta("history_since"))
            return {**result, "last_sync": cache.meta("last_sync"), "sync_error": sync_error,
                    "layout_mode": layout_mode, "layout_error": layout_error}

    @app.get("/api/overview/cached")
    async def cached_overview():
        cache = overview_store()
        active, completed, layout = cache.snapshot()
        result = build_overview(active, completed, layout, coverage_since=cache.meta("history_since"))
        return {**result, "available": bool(cache.meta("last_sync") or active or completed),
                "last_sync": cache.meta("last_sync"), "layout_mode": "ai" if layout else "fallback",
                "sync_error": None}

    async def overview_payload(body: OverviewTaskInput, token: str):
        project_ids = {item["id"] for item in await fetch_projects(token)}
        if body.project_id and body.project_id not in project_ids:
            raise ServiceError("Choose an existing Todoist project or Inbox.", 422)
        for value in (body.due_date, body.deadline_date):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise ServiceError("Use a real date in YYYY-MM-DD format.", 422) from None
        if body.due_date and body.deadline_date and body.due_date > body.deadline_date:
            raise ServiceError("The planned date must not be after the deadline.", 422)
        payload = {"content": body.content.strip(), "description": body.description,
                   "priority": body.priority}
        if not payload["content"]:
            raise ServiceError("A task title cannot be blank.", 422)
        if body.project_id: payload["project_id"] = body.project_id
        if body.due_date: payload["due_date"] = body.due_date
        if body.deadline_date: payload["deadline_date"] = body.deadline_date
        if body.duration_minutes:
            payload.update(duration=body.duration_minutes, duration_unit="minute")
        return payload

    @app.post("/api/overview/task")
    async def create_overview_task(body: OverviewTaskInput):
        token = await asyncio.to_thread(get_secret, "todoist")
        payload = await overview_payload(body, token)
        try:
            task_id = await create_task(token, payload)
        except TodoistWriteError as exc:
            raise ServiceError(str(exc), exc.status_code) from None
        overview_store().record_created_task(task_id, payload)
        return {"id": task_id}

    @app.post("/api/overview/task/{task_id}/edit")
    async def edit_overview_task(task_id: str, body: OverviewTaskEdit):
        token = await asyncio.to_thread(get_secret, "todoist")
        current = next((task for task in await fetch_tasks(token) if task.id == task_id), None)
        if current is None or current != body.expected:
            raise ServiceError("This task changed in Todoist. Refresh Overview before editing.", 409)
        await overview_payload(body, token)
        payload = {}
        if body.content.strip() != current.name: payload["content"] = body.content.strip()
        if body.description != current.description: payload["description"] = body.description
        if body.priority != current.priority: payload["priority"] = body.priority
        if body.due_date != (current.due[:10] if current.due else None):
            if body.due_date: payload["due_date"] = body.due_date
            else: payload["due_string"] = "no date"
        if body.deadline_date != current.deadline: payload["deadline_date"] = body.deadline_date
        if body.duration_minutes != current.duration_minutes:
            payload.update(duration=body.duration_minutes, duration_unit="minute" if body.duration_minutes else None)
        move_to = body.project_id if body.project_id and body.project_id != current.project_id else None
        if not body.project_id and current.project_id:
            raise ServiceError("Choose a destination project before moving this task.", 422)
        try:
            if payload: await write_existing_task(token, task_id, payload)
            if move_to: await move_task(token, task_id, move_to)
        except TodoistWriteError as exc:
            overview_store().invalidate()
            raise ServiceError("Some changes may already be saved. Refresh Overview and check Todoist. " + str(exc), exc.status_code) from None
        overview_store().record_updated_task(task_id, body, current)
        return {"id": task_id}

    @app.post("/api/overview/task/{task_id}/delete")
    async def delete_overview_task(task_id: str, expected: Task):
        token = await asyncio.to_thread(get_secret, "todoist")
        current = next((task for task in await fetch_tasks(token) if task.id == task_id), None)
        if current is None or current != expected:
            raise ServiceError("This task changed in Todoist. Refresh Overview before deleting.", 409)
        try:
            await delete_task(token, task_id)
        except TodoistWriteError as exc:
            overview_store().invalidate()
            raise ServiceError(str(exc), exc.status_code) from None
        overview_store().remove_task(task_id)
        return {"id": task_id}

    @app.post("/api/chat")
    async def chat_message(body: ChatRequest):
        sent_at = chat_now()
        if app.state.ai_lock.locked():
            raise ServiceError("Another AI request is running. Please wait.", 429)
        async with app.state.ai_lock:
            token = await asyncio.to_thread(get_secret, "todoist")
            key = await asyncio.to_thread(get_secret, settings.ai_key_account)
            tasks, project_options = await asyncio.gather(
                fetch_tasks(token), fetch_projects(token),
            )
            history = chat_store().recent() + [{"role": "user", "content": body.message.strip(), "created_at": sent_at}]
            result = await ai.chat(settings, key, history, tasks, project_options, timezone_name=body.timezone)
            chat_store().add_message("user", body.message.strip(), created_at=sent_at)
            # Dialogue state is a write-proposal gate, not merely wording in a prompt.
            if result.phase != "preview" or result.unresolved or result.clarification_question:
                reply = result.reply
                if result.clarification_question and result.clarification_question not in reply:
                    reply += "\n\n" + result.clarification_question
                chat_store().add_reply(reply, [])
                return {"reply": reply, "phase": "clarify" if result.unresolved else result.phase, "drafts": []}
            by_id = {task.id: task for task in tasks}
            actions = []
            seen = set()
            for action in result.actions:
                if action.task_id not in by_id or action.task_id in seen:
                    raise ServiceError("The AI selected an invalid task. Please try again.", 422)
                seen.add(action.task_id)
                changes = action.model_dump(include={"content", "description", "due_date", "deadline_date", "priority"}, exclude_none=True)
                if action.clear_due:
                    if action.due_date:
                        raise ServiceError("Conflicting due date changes. Please try again.", 422)
                    changes["due_string"] = "no date"
                if action.clear_deadline:
                    if action.deadline_date: raise ServiceError("Conflicting deadline changes.", 422)
                    changes["deadline_date"] = None
                if action.clear_duration:
                    if action.duration_minutes: raise ServiceError("Conflicting duration changes.", 422)
                    changes.update(duration=None, duration_unit=None)
                elif action.duration_minutes:
                    changes.update(duration=action.duration_minutes, duration_unit="minute")
                for proposed_date in (action.due_date, action.deadline_date):
                    try:
                        if proposed_date: date.fromisoformat(proposed_date)
                    except ValueError:
                        raise ServiceError("The AI proposed an invalid date. Please try again.", 422) from None
                if ("content" in changes and not changes["content"].strip()) or (action.operation == "update" and not changes) or (action.operation == "complete" and changes):
                    raise ServiceError("The AI proposed an invalid change. Please try again.", 422)
                actions.append({"operation": action.operation, "task_id": action.task_id,
                                "reason": action.reason, "before": by_id[action.task_id].model_dump(), "changes": changes})
            saved = chat_store().add_reply(result.reply, result.drafts, actions)
            return {"reply": result.reply, "drafts": saved}

    @app.post("/api/chat/action")
    async def decide_chat_action(body: ChatActionDecision):
        store = chat_store()
        saved = store.action(body.id)
        if not saved or saved["state"] != "pending":
            raise ServiceError("This action is no longer pending. Refresh chat.", 409)
        if body.decision == "cancel":
            if not store.transition_action(body.id, "pending", "cancelled"):
                raise ServiceError("This action is already being processed.", 409)
            return {"state": "cancelled"}
        proposal = saved["payload"]
        token = await asyncio.to_thread(get_secret, "todoist")
        current = next((task for task in await fetch_tasks(token) if task.id == proposal["task_id"]), None)
        if current is None or current.model_dump() != proposal["before"]:
            store.transition_action(body.id, "pending", "stale", "Task changed or finished. Ask Chat for a fresh preview.")
            raise ServiceError("This task changed or was completed since the preview. Ask Chat for a fresh preview.", 409)
        if not store.transition_action(body.id, "pending", "applying"):
            raise ServiceError("This action is already being processed.", 409)
        try:
            await write_existing_task(token, proposal["task_id"], proposal["changes"],
                                      complete=proposal["operation"] == "complete")
        except TodoistWriteError as exc:
            state = "uncertain" if exc.uncertain else "pending"
            store.transition_action(body.id, "applying", state, str(exc))
            overview_store().invalidate()
            raise ServiceError(str(exc), exc.status_code) from None
        except Exception:
            store.transition_action(body.id, "applying", "uncertain", "Check Todoist before retrying.")
            overview_store().invalidate()
            raise ServiceError("Could not confirm the result. Check Todoist before retrying.") from None
        store.transition_action(body.id, "applying", "applied")
        overview_store().invalidate()
        store.add_message("assistant", "Confirmed in Todoist: {} — {}".format(proposal["operation"], current.name))
        return {"state": "applied"}

    @app.post("/api/chat/confirm")
    async def confirm_drafts(body: ConfirmDraftsRequest):
        if app.state.ai_lock.locked():
            raise ServiceError("Another AI request is running. Please wait.", 429)
        ids = [draft.id for draft in body.drafts]
        if len(ids) != len(set(ids)):
            raise ServiceError("Select each draft only once.", 422)
        token = await asyncio.to_thread(get_secret, "todoist")
        allowed_projects = {item["id"] for item in await fetch_projects(token)}
        for draft in body.drafts:
            if draft.project_id is not None and draft.project_id not in allowed_projects:
                raise ServiceError("Choose an existing Todoist project or Inbox.", 422)
            for proposed_date in (draft.due_date, draft.deadline_date):
                try:
                    if proposed_date: date.fromisoformat(proposed_date)
                except ValueError:
                    raise ServiceError("Use a real due date in YYYY-MM-DD format.", 422) from None
            if draft.due_date and draft.deadline_date and draft.due_date > draft.deadline_date:
                raise ServiceError("The planned date must not be after the deadline.", 422)
        results = []
        for draft in body.drafts:
            payload = draft.model_dump(exclude={"id"}, exclude_none=True)
            duration = payload.pop("duration_minutes", None)
            if duration: payload.update(duration=duration, duration_unit="minute")
            payload["content"] = payload["content"].strip()
            if not payload["content"]:
                raise ServiceError("A task title cannot be blank.", 422)
            if not chat_store().claim(draft.id, payload):
                results.append({"id": draft.id, "state": "not_pending", "message": "Already created or unavailable."})
                continue
            try:
                task_id = await create_task(token, payload)
            except TodoistWriteError as exc:
                state = "uncertain" if exc.uncertain else "pending"
                chat_store().finish(draft.id, state, error=str(exc))
                results.append({"id": draft.id, "state": state, "message": str(exc)})
            else:
                chat_store().finish(draft.id, "created", task_id=task_id)
                overview_store().record_created_task(task_id, payload)
                results.append({"id": draft.id, "state": "created", "task_id": task_id})
        created = sum(item["state"] == "created" for item in results)
        if created:
            overview_store().invalidate()
            chat_store().add_message("assistant", "已确认创建 {} 条 Todoist 待办。无日期事项在 Overview → All 查看；正在自动更新 Overview。".format(created))
        return {"results": results}

    @app.post("/api/chat/draft/cancel")
    async def cancel_chat_draft(body: ChatActionDecision):
        if body.decision != "cancel" or not chat_store().cancel_draft(body.id):
            raise ServiceError("This draft is no longer pending.", 409)
        return {"state": "cancelled"}

    @app.post("/api/chat/clear")
    async def clear_chat():
        if not chat_store().clear():
            raise ServiceError("Wait for Todoist task creation to finish before clearing chat.", 409)
        return {"cleared": True}

    @app.post("/api/recommend")
    async def recommendations(body: RecommendationRequest):
        if app.state.ai_lock.locked():
            raise ServiceError("A request is already running. Please wait for it to finish.", 429)
        async with app.state.ai_lock:
            token = await asyncio.to_thread(get_secret, "todoist")
            tasks = await fetch_tasks(token)
            candidates = select_candidates(tasks, settings.important_projects, exclude_ids=set(body.exclude_ids))
            if not candidates:
                return {
                    "recommendations": [], "total_tasks": len(tasks), "candidate_count": 0,
                    "message": "No other tasks to suggest. Try Find My Next Action again." if tasks else "No active tasks in Todoist. You're all caught up.",
                }
            key = await asyncio.to_thread(get_secret, settings.ai_key_account)
            result = await ai.recommend(settings, key, candidates, body)
            tasks_by_id = {task.id: task for task in candidates}
            suggestions.clear()
            cards = []
            for action in result.recommendations:
                task = tasks_by_id[action.task_id]
                recommendation_id = uuid4().hex
                suggestions[recommendation_id] = (task, action, time.monotonic())
                cards.append({
                    **action.model_dump(), "recommendation_id": recommendation_id,
                    "task_name": task.name, "project_name": task.project_name,
                    "due": task.due, "deadline": task.deadline,
                    "priority": "P{}".format(5 - task.priority),
                })
            return {"recommendations": cards, "total_tasks": len(tasks), "candidate_count": len(candidates)}

    @app.post("/api/easier")
    async def easier(body: EasierRequest):
        saved = suggestions.get(body.recommendation_id)
        if saved is None or time.monotonic() - saved[2] > 1800:
            raise ServiceError("This suggestion has expired. Find your next action again.", 409)
        if app.state.ai_lock.locked():
            raise ServiceError("A request is already running. Please wait for it to finish.", 429)
        async with app.state.ai_lock:
            key = await asyncio.to_thread(get_secret, settings.ai_key_account)
            result = await ai.make_easier(settings, key, saved[0], saved[1], body)
            return result.model_dump()

    @app.post("/api/recommend/complete")
    async def complete_recommendation(body: RecommendationAction):
        task = suggested_task(body.recommendation_id)
        token = await asyncio.to_thread(get_secret, "todoist")
        active = {item.id for item in await fetch_tasks(token)}
        if task.id not in active:
            raise ServiceError("This task is no longer active. Refresh recommendations.", 409)
        suggestions.pop(body.recommendation_id, None)
        await write_existing_task(token, task.id, complete=True)
        overview_store().invalidate()
        return {"completed": True, "task_id": task.id}

    @app.post("/api/recommend/progress")
    async def propose_progress(body: ProgressRequest):
        task = suggested_task(body.recommendation_id)
        if app.state.ai_lock.locked():
            raise ServiceError("Another AI request is running. Please wait.", 429)
        async with app.state.ai_lock:
            key = await asyncio.to_thread(get_secret, settings.ai_key_account)
            plan = await ai.plan_progress(settings, key, task, body.progress.strip())
            if any(draft.project_id not in (None, task.project_id) for draft in plan.new_tasks):
                raise ServiceError("The AI selected an unrelated project. Please try again.")
            proposal_id = uuid4().hex
            progress_proposals[proposal_id] = (task, plan, time.monotonic())
            return {"proposal_id": proposal_id, **plan.model_dump(), "task_name": task.name,
                    "current_description": task.description}

    @app.post("/api/recommend/progress/confirm")
    async def confirm_progress(body: ProgressConfirm):
        saved = progress_proposals.get(body.proposal_id)
        if saved is None or time.monotonic() - saved[2] > 1800:
            raise ServiceError("This progress preview expired. Describe your progress again.", 409)
        task, _, _ = saved
        token = await asyncio.to_thread(get_secret, "todoist")
        active = {item.id: item for item in await fetch_tasks(token)}
        current = active.get(task.id)
        if current is None:
            raise ServiceError("This task is no longer active. Refresh recommendations.", 409)
        if current.name != task.name or current.description != task.description:
            raise ServiceError("This task changed in Todoist. Refresh and review it again.", 409)
        allowed_projects = {item["id"] for item in await fetch_projects(token)}
        for draft in body.new_tasks:
            if draft.project_id is not None and draft.project_id not in allowed_projects:
                raise ServiceError("Choose an existing Todoist project or Inbox.", 422)
            if draft.due_date:
                try:
                    date.fromisoformat(draft.due_date)
                except ValueError:
                    raise ServiceError("Use a real due date in YYYY-MM-DD format.", 422) from None
        progress_proposals.pop(body.proposal_id, None)
        changes = {}
        if body.updated_title is not None and body.updated_title.strip() != task.name:
            changes["content"] = body.updated_title.strip()
        if body.updated_description is not None and body.updated_description != task.description:
            changes["description"] = body.updated_description
        created = []
        try:
            if changes:
                await write_existing_task(token, task.id, payload=changes)
            for draft in body.new_tasks:
                created.append(await create_task(token, draft.model_dump(exclude_none=True)))
            if body.complete:
                await write_existing_task(token, task.id, complete=True)
        except TodoistWriteError as exc:
            overview_store().invalidate()
            raise ServiceError("Some progress changes may already be saved in Todoist. Check the task "
                               "and any new tasks before trying again. " + str(exc), exc.status_code) from None
        overview_store().invalidate()
        return {"updated": bool(changes), "completed": body.complete, "created_task_ids": created}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
