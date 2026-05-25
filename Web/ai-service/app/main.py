"""
Grid-Guardian AI Decision Engine - FastAPI Microservice

This is the main entry point for the AI service that handles:
- Telemetry prediction
- Trading decision generation
- Dynamic policy selection (BC, CQL, DT)
- Safety enforcement
- Edge deployment for Raspberry Pi
"""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logger import get_logger
from app.models.registry import model_registry
from app.api.routes import router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle management."""
    # Startup
    logger.info("=" * 60)
    logger.info("Starting Grid-Guardian AI Decision Engine")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Model artifacts path: {settings.MODEL_ARTIFACTS_PATH}")
    logger.info("=" * 60)

    # Initialize model registry
    try:
        await model_registry.initialize()
        logger.info("Model registry initialized successfully")
        logger.info(f"Available models: {list(model_registry.get_available_models().keys())}")
    except Exception as e:
        logger.error(f"Failed to initialize model registry: {e}")
        logger.warning("Service will start in degraded mode with fallback behavior")

    yield

    # Shutdown
    logger.info("Shutting down AI Decision Engine...")
    await model_registry.shutdown()
    logger.info("AI Decision Engine shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="Grid-Guardian AI Decision Engine",
    description="""
    AI microservice for Grid-Guardian energy trading system.

    Features:
    - Dynamic policy selection (BC, CQL, DT)
    - Real-time trading decisions
    - Safety enforcement layer
    - Edge deployment ready for Raspberry Pi
    """,
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router, prefix="/api/v1")

# Root endpoint
@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "Grid-Guardian AI Decision Engine",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


# Health check (duplicate at root level for convenience)
@app.get("/health")
async def health_check():
    """Quick health check endpoint."""
    models = model_registry.get_available_models()
    return {
        "status": "healthy" if any(models.values()) else "degraded",
        "models_loaded": sum(1 for v in models.values() if v),
        "total_models": len(models),
    }


if __name__ == "__main__":
    host = os.getenv("AI_HOST", "0.0.0.0")
    port = int(os.getenv("AI_PORT", 8000))
    reload = os.getenv("AI_RELOAD", "false").lower() == "true"

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=settings.LOG_LEVEL.lower(),
    )
