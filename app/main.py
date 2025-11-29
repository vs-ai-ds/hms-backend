from fastapi import FastAPI

from app.core.config import get_settings
from app.api.v1.router import api_router

settings = get_settings()

app = FastAPI(
    title="Hospital Management System Backend",
)


@app.get("/health", tags=["health"])
async def root_health() -> dict:
    """
    Global health check endpoint.
    """
    return {"status": "ok"}


# Mount versioned API router
app.include_router(api_router, prefix=settings.api_v1_prefix)