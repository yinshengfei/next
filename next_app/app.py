import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import ai
from .candidates import select_candidates
from .config import Settings, SetupError, get_secret, load_settings
from .models import EasierRequest, RecommendationRequest
from .todoist import ServiceError, fetch_tasks


STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app):
        # Python 3.9 binds locks at creation; create this inside the running loop.
        app.state.ai_lock = asyncio.Lock()
        yield

    app = FastAPI(title="Next", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    # Only the latest three task snapshots live in memory. No local task database.
    suggestions = {}

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
        return JSONResponse({"detail": "Choose 5–480 whole minutes and a valid energy level."}, status_code=422)

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

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
