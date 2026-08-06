import asyncio
import os
from typing import Optional

from fastapi import FastAPI

from app.db import get_db_conn
from app.services.scan import scan_watch_folder

TICK_SECONDS = 60


def should_run_now(watch_folder_row: dict, now_ms_value: int) -> bool:
    if not watch_folder_row.get("enabled"):
        return False
    if watch_folder_row.get("lastScanAt") is None:
        return True
    elapsed_ms = now_ms_value - watch_folder_row["lastScanAt"]
    return elapsed_ms >= watch_folder_row["frequencyMinutes"] * 60 * 1000


def scheduler_tick() -> dict:
    from app.db import now_ms

    conn = get_db_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM watch_folders")]
    conn.close()

    now_value = now_ms()
    scanned = 0
    total_ingested = 0
    for row in rows:
        if should_run_now(row, now_value):
            total_ingested += scan_watch_folder(row)
            scanned += 1

    return {"watchFoldersScanned": scanned, "totalIngested": total_ingested}


async def _scheduler_loop():
    while True:
        try:
            # scheduler_tick() is synchronous, blocking file I/O (os.walk +
            # reading file contents). Confirmed live: a watch folder pointed
            # at a Dropbox-synced directory made a single tick take many
            # minutes, since Dropbox has to download each file's content on
            # first read — and because this used to run directly on the
            # event loop, that froze the entire process, including uvicorn's
            # own startup, for the whole scan. to_thread keeps a slow scan
            # from blocking anything else the server needs to do.
            await asyncio.to_thread(scheduler_tick)
        except Exception:
            pass  # one bad tick must never kill the loop
        await asyncio.sleep(TICK_SECONDS)


_background_task: Optional[asyncio.Task] = None


def start_scheduler(app: FastAPI) -> None:
    """Registers the background scan loop on the app's startup/shutdown events.
    Gated by DISABLE_SCHEDULER=1 — set by tests/conftest.py's client fixture so
    that merely instantiating the app in a test never spins up a background
    task that reaches out to real watched folders on this machine while a
    test is using a temp DB.
    """
    if os.getenv("DISABLE_SCHEDULER") == "1":
        return

    @app.on_event("startup")
    async def _on_startup():
        global _background_task
        _background_task = asyncio.create_task(_scheduler_loop())

    @app.on_event("shutdown")
    async def _on_shutdown():
        if _background_task:
            _background_task.cancel()
