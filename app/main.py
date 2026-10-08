"""Single-process FastAPI entry point and static dashboard."""

import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .database import Database
from .schemas import CreateTimeline, PatchTimeline, SetTime
from .service import TimelineNotFound, TimelineService, system_clock_ms
from .temporal import TemporalRangeError

logger = logging.getLogger("uvicorn.error")
STATIC = Path(__file__).parent / "static"


def create_app(
    database_path: str | Path | None = None,
    clock: Callable[[], int] = system_clock_ms,
) -> FastAPI:
    database = Database(database_path or os.environ.get("CHRONOS_DB_PATH", "data/timelines.db"))
    service = TimelineService(database, clock)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database.initialize()
        logger.info("Chronos ready; SQLite WAL initialized without resetting timeline anchors")
        yield

    application = FastAPI(
        title="Chronos", version="1.0.0", debug=False, lifespan=lifespan,
        docs_url=None, redoc_url=None,
    )
    application.state.database = database
    application.state.service = service

    @application.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(TimelineNotFound)
    async def not_found(request: Request, exc: TimelineNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(TemporalRangeError)
    async def invalid_instant(request: Request, exc: TemporalRangeError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(sqlite3.Error)
    async def database_failure(request: Request, exc: sqlite3.Error):
        logger.error("SQLite request failed", exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse(status_code=500, content={"detail": "Errore interno del database."})

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Omit raw input/ctx: they may contain NaN, which is not valid response JSON.
        errors = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @application.get("/api/health")
    def health():
        try:
            if database.healthy():
                return {"status": "ok", "database": "ok"}
        except sqlite3.Error:
            logger.exception("SQLite healthcheck failed")
        return JSONResponse(status_code=503, content={"status": "error", "database": "error"})

    @application.get("/api/timelines")
    def list_timelines():
        return service.list()

    @application.post("/api/timelines", status_code=201)
    def create_timeline(body: CreateTimeline, response: Response):
        result = service.create(body.name, body.start_time, body.speed, body.status)
        response.headers["Location"] = f"/api/timelines/{result['id']}"
        return result

    @application.get("/api/timelines/{timeline_id}")
    def get_timeline(timeline_id: str):
        return service.get(timeline_id)

    @application.get("/api/timelines/{timeline_id}/now")
    def get_now(timeline_id: str):
        result = service.get(timeline_id)
        return {field: result[field] for field in ("id", "virtual_now", "real_now", "speed", "status", "time_error")}

    @application.patch("/api/timelines/{timeline_id}")
    def patch_timeline(timeline_id: str, body: PatchTimeline):
        return service.patch(timeline_id, body.model_dump(exclude_unset=True))

    @application.put("/api/timelines/{timeline_id}/time")
    def set_time(timeline_id: str, body: SetTime):
        return service.set_time(timeline_id, body.virtual_time)

    @application.post("/api/timelines/{timeline_id}/pause")
    def pause(timeline_id: str):
        return service.set_status(timeline_id, "paused")

    @application.post("/api/timelines/{timeline_id}/resume")
    def resume(timeline_id: str):
        return service.set_status(timeline_id, "running")

    @application.delete("/api/timelines/{timeline_id}", status_code=204)
    def delete(timeline_id: str):
        service.delete(timeline_id)
        return Response(status_code=204)

    @application.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    application.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")
    return application


app = create_app()
