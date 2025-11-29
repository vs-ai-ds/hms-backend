# app/services/sharing_service.py
from uuid import UUID

from sqlalchemy.orm import Session

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
    """
    req = SharingRequest(
        from_tenant_id=from_tenant_id,
        to_tenant_id=payload.to_tenant_id,
        patient_global_id=payload.patient_global_id,
        reason=payload.reason,
        status=SharingStatus.PENDING_PATIENT,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req