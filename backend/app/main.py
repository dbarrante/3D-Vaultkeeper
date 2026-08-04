import os
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from app.db import init_db, UPLOAD_DIR, WEBUI_URL
from app.routers import folders, models, manuals, settings, importers, watcher, inbox, ai, import_wizard, file_view
from app.scheduler import start_scheduler

init_db()

app = FastAPI(title="STLVault API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development, or use [WEBUI_URL] for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(folders.router)
app.include_router(models.router)
app.include_router(manuals.router)
app.include_router(settings.router)
app.include_router(importers.router)
app.include_router(watcher.router)
app.include_router(inbox.router)
app.include_router(ai.router)
app.include_router(import_wizard.router)
app.include_router(file_view.router)

start_scheduler(app)


def _frontend_dist_dir() -> Path:
    """The built frontend's static files. In a frozen desktop build,
    PyInstaller's `datas` bundling (see desktop/launcher.spec) places it
    under sys._MEIPASS/frontend_dist — that exact name must match on both
    sides. In a normal dev checkout it's the sibling frontend/dist/ that
    `bun run build` produces, which usually doesn't exist (nobody builds
    the frontend to run the backend test suite) — the mount below is
    skipped entirely in that case, exactly like it always has been.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "frontend_dist"
    return Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


FRONTEND_DIST = _frontend_dist_dir()
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    # Ensure upload directory exists
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    port = int(os.getenv("PORT", "5173"))

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info"
    )
