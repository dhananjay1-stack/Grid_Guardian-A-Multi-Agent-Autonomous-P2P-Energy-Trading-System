"""
Grid-Guardian AI Decision Engine Service

FastAPI-based microservice for intelligent energy trading decisions.

Features:
- Dynamic policy selection (BC/CQL/DT)
- Condition-based routing
- Safety constraints enforcement
- Production-ready inference
- Raspberry Pi edge deployment support
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.api.routes import router
from app.services.decision_engine import decision_engine
from app.core.config import settings
from app.core.logger import get_logger


logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    # Startup
    logger.info("Starting Grid-Guardian AI Decision Engine...")
    logger.info(f"Model base path: {settings.model_base_path}")
    logger.info(f"Policy pack path: {settings.policy_pack_path}")

    try:
        success = decision_engine.initialize()
        if success:
            logger.info("Decision Engine initialized successfully")
        else:
            logger.warning("Decision Engine initialized with warnings - some models may be unavailable")
    except Exception as e:
        logger.error(f"Failed to initialize Decision Engine: {e}")
        logger.warning("Starting server without models - inference will use fallback")

    yield

    # Shutdown
    logger.info("Shutting down AI Decision Engine...")


# Create FastAPI application
app = FastAPI(
    title="Grid-Guardian AI Decision Engine",
    description="""
    AI-powered decision engine for Grid-Guardian energy trading system.

    ## Features

    - **Dynamic Policy Selection**: Automatically selects the best policy (BC/CQL/DT)
      based on current operating conditions
    - **Condition Detection**: Analyzes telemetry to assess risk, volatility, and system state
    - **Safety Shield**: Enforces SoC limits, power constraints, and confidence thresholds
    - **Edge Deployment**: Optimized for Raspberry Pi 5 (ARM64)

    ## Policies

    - **DT (Decision Transformer)**: Best for stable conditions with long-horizon planning
    - **CQL (Conservative Q-Learning)**: Best for uncertain/risky conditions
    - **BC (Behavior Cloning)**: Safe fallback for degraded conditions

    ## Endpoints

    - `POST /predict`: Get forecasts without making decisions
    - `POST /decide`: Get trading/control decisions
    - `GET /health`: Service health check
    - `GET /model/status`: Model availability and metadata
    - `GET /metrics`: Service performance metrics
    """,
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router, prefix="", tags=["AI Decision Engine"])


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "Grid-Guardian AI Decision Engine",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs"
    }


def main():
    """Run the server."""
    logger.info(f"Starting server on {settings.host}:{settings.port}")
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        workers=settings.workers if not settings.debug else 1
    )


if __name__ == "__main__":
    main()
