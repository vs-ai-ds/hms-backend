# app/main.py
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.redis import is_redis_available
from app.services.seed_service import seed_permission_definitions

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title="Hospital Management System Backend",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    """Initialize app on startup."""
    # Test Redis connectivity
    if settings.redis_url:
        if is_redis_available():
            logger.info("Redis is available. Caching enabled.")
        else:
            logger.warning(
                "Redis is configured but unavailable. Running in degraded mode (no caching)."
            )
    else:
        logger.info("Redis not configured. Running without caching.")

    # Seed permission definitions in public schema
    # Note: Public schema tables should be created via Alembic migrations (alembic upgrade head)
    # This startup event only seeds data, not schema
    db = SessionLocal()
    try:
        seed_permission_definitions(db)
        db.commit()
        logger.info("Permission definitions seeded successfully.")
    except Exception as e:
        logger.error(f"Failed to seed permission definitions: {e}")
        db.rollback()
        raise
    finally:
        db.close()


@app.get("/health", tags=["health"])
async def root_health() -> dict:
    """
    Global health check endpoint.
    """
    return {"status": "ok"}


app.include_router(api_router, prefix=settings.api_v1_prefix)
