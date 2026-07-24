"""
Quant Engine — FastAPI entry point.
Runs on port 5001 alongside the Node.js app on port 3000.
"""
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from quant_engine.config import HOST, PORT
from quant_engine.routers import scores, index_analysis, backtest, sicilian, ml, signal_quality, sentiment, data_sync

app = FastAPI(
    title="Stock Quant Engine",
    description="Multi-factor scoring engine for systematic long/short signals.",
    version="1.0.0",
)

# Allow the Node.js frontend to call us
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(scores.router)
app.include_router(index_analysis.router)
app.include_router(backtest.router)
app.include_router(sicilian.router)
app.include_router(ml.router)
app.include_router(signal_quality.router)
app.include_router(sentiment.router)
app.include_router(data_sync.router)


@app.get("/health")
def health_check():
    return {"status": "ok", "engine": "quant", "port": PORT}


@app.on_event("startup")
def _warm_ic_weights():
    """
    Pre-populate the IC weights cache so the first user request hits a warm
    cache instead of paying the ~2-3s panel-build cost. Failures are logged
    but swallowed so a transient Turso outage at boot doesn't crash the
    process.

    Runs on a background thread. Starlette calls a non-async startup handler
    inline, so doing this work here directly blocked the event loop and the
    server could not answer /health until it finished — measured at ~20s
    against a real Turso instance, which is what timed out the GitHub Actions
    health probe (run 30130881196). The warm-up is a latency optimisation, not
    a correctness requirement, so nothing should wait on it.

    _cache in ic_weights.py is already guarded by a threading.Lock, so a
    request arriving mid-warm-up is safe: it either reads the warm value or
    computes its own and last-write-wins on an identical result.
    """
    import logging
    import threading
    log = logging.getLogger(__name__)

    def _warm():
        try:
            from quant_engine.scoring.ic_weights import get_active_weights
            get_active_weights()
            log.info("IC weights pre-warmed at startup")
        except Exception as exc:
            log.warning("IC weights warmup failed: %s", exc)

    # daemon=True so a hung Turso call can never hold up interpreter shutdown
    threading.Thread(target=_warm, name="ic-weights-warmup", daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("quant_engine.main:app", host=HOST, port=PORT, reload=True)
