# app/api/v1/endpoints/documents.py
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File,
    status,
    Query,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.schemas.document import DocumentResponse
from app.services.document_service import (
    create_document_for_patient,
    list_documents_for_patient,
    get_document,
    PatientNotFoundError,
    DocumentNotFoundError,
)
from app.utils.file_storage import resolve_storage_path

router = APIRouter()


@router.post(
    "/",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    patient_id: UUID = Query(..., description="ID of the patient"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> DocumentResponse:
    """
    Upload a document for a patient in the current tenant.

    - Stores the file on disk.
    - Creates a Document record in the tenant schema.
    """
    try:
        file_bytes = await file.read()
        doc = create_document_for_patient(
            db=db,
            schema_name=ctx.tenant.schema_name,
            patient_id=patient_id,
            uploaded_by_id=ctx.user.id,
            file_bytes=file_bytes,
            original_filename=file.filename,
            mime_type=file.content_type,
        )
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save document.",
        )

    return DocumentResponse.model_validate(doc)


@router.get(
    "/",
    response_model=list[DocumentResponse],
)
def list_patient_documents(
    patient_id: UUID = Query(..., description="ID of the patient"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[DocumentResponse]:
    """
    List documents for a patient in the current tenant.
    """
    docs = list_documents_for_patient(db=db, patient_id=patient_id)
    return [DocumentResponse.model_validate(d) for d in docs]


@router.get("/{document_id}/download")
def download_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """
    Download a specific document.

    Access is tenant-scoped via search_path and patient relationship.
    """
    try:
        doc = get_document(db=db, document_id=document_id)
    except DocumentNotFoundError:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = resolve_storage_path(doc.storage_path)

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found on storage.",
        )

    return FileResponse(
        path=str(file_path),
        media_type=doc.mime_type or "application/octet-stream",
        filename=doc.file_name,
    )