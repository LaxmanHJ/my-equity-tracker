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
from quant_engine.routers import scores, index_analysis, backtest, sicilian, ml, signal_quality

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


@app.get("/health")
def health_check():
    return {"status": "ok", "engine": "quant", "port": PORT}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("quant_engine.main:app", host=HOST, port=PORT, reload=True)
