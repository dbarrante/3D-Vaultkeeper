import os
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.db import init_db, UPLOAD_DIR, WEBUI_URL
from app.routers import folders, models, manuals, settings, importers, watcher, inbox, ai
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

start_scheduler(app)


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
