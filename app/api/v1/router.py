# app/api/v1/router.py
from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    admissions,
    appointments,
    auth,
    dashboard,
    departments,
    documents,
    patient_shares,
    patients,
    platform_tenants,
    prescriptions,
    roles,
    sharing,
    stock_items,
    tenants,
    users,
    vitals,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
api_router.include_router(patients.router, prefix="/patients", tags=["patients"])
from app.api.v1.endpoints import patients_export

api_router.include_router(patients_export.router, prefix="/patients", tags=["patients"])
api_router.include_router(
    appointments.router, prefix="/appointments", tags=["appointments"]
)
api_router.include_router(
    prescriptions.router, prefix="/prescriptions", tags=["prescriptions"]
)
api_router.include_router(admissions.router, prefix="/admissions", tags=["admissions"])
api_router.include_router(vitals.router, prefix="/vitals", tags=["vitals"])
api_router.include_router(
    platform_tenants.router, prefix="/platform/tenants", tags=["platform-tenants"]
)
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(sharing.router, prefix="/sharing", tags=["sharing"])
api_router.include_router(
    departments.router, prefix="/departments", tags=["departments"]
)
api_router.include_router(roles.router, prefix="/roles", tags=["roles"])
api_router.include_router(
    stock_items.router, prefix="/stock-items", tags=["stock_items"]
)
api_router.include_router(
    patient_shares.router, prefix="/patient-shares", tags=["patient-shares"]
)
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
