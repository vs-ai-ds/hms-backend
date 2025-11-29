# app/api/v1/router.py
from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    tenants,
    patients,
    appointments,
    prescriptions,
    documents,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
api_router.include_router(patients.router, prefix="/patients", tags=["patients"])
api_router.include_router(appointments.router, prefix="/appointments", tags=["appointments"])
api_router.include_router(prescriptions.router, prefix="/prescriptions", tags=["prescriptions"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])