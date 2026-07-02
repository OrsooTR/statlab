"""StatLab application factory: mounts both module routers and the SPA."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .core import jobs
from .core.config import APP_NAME, APP_VERSION, STATIC_DIR, ensure_dirs
from .core.database import init_db
from .core.system_api import router as system_router
from .crazytime.api import router as crazytime_router
from .football.api import router as football_router
from .football.live.api import router as live_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    init_db()
    yield
    jobs.manager.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict:
        return {"app": APP_NAME, "version": APP_VERSION, "status": "ok"}

    app.include_router(crazytime_router)
    app.include_router(football_router)
    app.include_router(live_router)
    app.include_router(system_router)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
