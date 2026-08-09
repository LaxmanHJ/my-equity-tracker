"""
CATALAN — FastAPI entry point. Port 5100.

Standalone: serves both its own API and its own UI. It does not register with
the Node app on 3000 and shares nothing with the quant engine on 5001 except
read-only access to Turso.

Run:
    python3 -m uvicorn catalan.main:app --host 0.0.0.0 --port 5100 --reload
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from catalan.config import DB_PATH, HOST, PORT, STUDY_VERSION, UI_DIR
from catalan.data.store import store, turso_reachable
from catalan.routers import study

app = FastAPI(
    title="CATALAN",
    description="LLM news-reaction study on Indian equities. Standalone research service.",
    version=STUDY_VERSION,
)

app.include_router(study.router)


@app.get("/health")
def health() -> dict:
    """Reports enough to tell a dead collector from a dead service."""
    try:
        conn = store()
        n = conn.execute("SELECT count(*) FROM announcements").fetchone()[0]
        conn.close()
        store_ok, announcements = True, n
    except Exception as exc:                       # noqa: BLE001 — health must not raise
        store_ok, announcements = False, str(exc)

    return {
        "status": "ok" if store_ok else "degraded",
        "service": "catalan",
        "study": STUDY_VERSION,
        "port": PORT,
        "store": str(DB_PATH),
        "store_ok": store_ok,
        "announcements": announcements,
        "turso_readable": turso_reachable(),
    }


# UI last: mounting StaticFiles at "/" would otherwise shadow the routes above.
if UI_DIR.is_dir():
    app.mount("/css", StaticFiles(directory=UI_DIR / "css"), name="css")
    app.mount("/js", StaticFiles(directory=UI_DIR / "js"), name="js")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("catalan.main:app", host=HOST, port=PORT, reload=True)
