# app/services/sharing_service.py

from __future__ import annotations
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.models.sharing import SharingRequest, SharingStatus
from app.schemas.sharing import SharingRequestCreate


def create_sharing_request(
    db: Session,
    *,
    from_tenant_id: UUID,
    payload: SharingRequestCreate,
) -> SharingRequest:
    """
    Create a cross-tenant sharing request.

    Note:
    We keep commit here for now (so endpoint stays simple), but we always rollback on failure
    to avoid leaving the session in a broken transaction state.
    """
    req = SharingRequest(
        from_tenant_id=from_tenant_id,
        to_tenant_id=payload.to_tenant_id,
        patient_global_id=payload.patient_global_id,
        reason=payload.reason,
        status=SharingStatus.PENDING_PATIENT,
    )

    try:
        db.add(req)
        db.commit()
        db.refresh(req)
        return req
    except SQLAlchemyError:
        db.rollback()
        raise