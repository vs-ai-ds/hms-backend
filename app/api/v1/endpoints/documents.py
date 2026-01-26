# app/api/v1/endpoints/documents.py
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenant_context import TenantContext, get_tenant_context
from app.core.tenant_db import ensure_search_path
from app.schemas.document import DocumentResponse
from app.services.document_service import (
    DocumentNotFoundError,
    PatientNotFoundError,
    create_document_for_patient,
    delete_document,
    get_document,
    list_documents_for_patient,
)
from app.services.tenant_service import ensure_tenant_tables_exist
from app.utils.file_storage import resolve_storage_path

router = APIRouter()


@router.post(
    "",
    response_model=list[DocumentResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upload_documents(
    patient_id: UUID = Query(..., description="ID of the patient"),
    files: list[UploadFile] = File(...),
    document_types: list[str] = Query(
        default=[], description="Document types for each file (same order as files)"
    ),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[DocumentResponse]:
    """
    Upload one or more documents (up to 10) for a patient in the current tenant.

    - Stores each file on disk.
    - Creates a Document record in the tenant schema for each file.
    - Max 10 files per upload, 10MB per file, 50MB total.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    ensure_tenant_tables_exist(db, ctx.tenant.schema_name)
    ensure_search_path(db, ctx.tenant.schema_name)
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files were provided.",
        )

    if len(files) > 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can upload a maximum of 10 documents at a time.",
        )

    # File size validation
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
    MAX_TOTAL_SIZE = 50 * 1024 * 1024  # 50MB
    total_size = 0

    # Validate file sizes before processing
    file_sizes = []
    for file in files:
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One of the files is missing a filename.",
            )

        # Read file to get size
        file_bytes = await file.read()
        file_size = len(file_bytes)
        file_sizes.append((file, file_bytes, file_size))

        if file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File '{file.filename}' exceeds the maximum size of 10MB.",
            )

        total_size += file_size
        # Reset file pointer for later use
        await file.seek(0)

    if total_size > MAX_TOTAL_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Total file size ({total_size / (1024 * 1024):.2f}MB) exceeds the maximum of 50MB.",
        )

    allowed_extensions = {
        # Documents
        ".pdf",
        ".doc",
        ".docx",
        ".txt",
        ".rtf",
        ".odt",  # OpenDocument Text
        # Spreadsheets
        ".xls",
        ".xlsx",
        ".csv",
        ".ods",  # OpenDocument Spreadsheet
        # Presentations
        ".ppt",
        ".pptx",
        ".odp",  # OpenDocument Presentation
        # Images
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tiff",
        ".tif",
        ".webp",
        ".svg",
        # Medical Imaging
        ".dcm",
        ".dicom",
        # Archives (for compressed medical records)
        ".zip",
        ".rar",
        # Web formats
        ".html",
        ".htm",
        # Audio (for voice notes/recordings)
        ".mp3",
        ".wav",
        ".m4a",
        ".ogg",
        # Video (for medical procedure recordings)
        ".mp4",
        ".avi",
        ".mov",
        ".wmv",
        ".mkv",
        # Structured data
        ".xml",
        ".json",
    }

    docs: list[DocumentResponse] = []

    try:
        from pathlib import Path

        for idx, (file, file_bytes, file_size) in enumerate(file_sizes):
            ext = Path(file.filename).suffix.lower()
            if ext not in allowed_extensions:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File type '{ext}' is not allowed.",
                )

            # Get document type for this file (if provided)
            document_type = document_types[idx] if idx < len(document_types) else None

            doc = create_document_for_patient(
                db=db,
                schema_name=ctx.tenant.schema_name,
                patient_id=patient_id,
                uploaded_by_id=ctx.user.id,
                file_bytes=file_bytes,
                original_filename=file.filename,
                mime_type=file.content_type,
                document_type=document_type,
            )
            docs.append(DocumentResponse.model_validate(doc))
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save document(s).",
        )

    return docs


@router.get(
    "",
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
    ensure_search_path(db, ctx.tenant.schema_name)
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
    ensure_search_path(db, ctx.tenant.schema_name)
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


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_patient_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant_context),
) -> Response:
    """
    Delete a specific document and its stored file.
    """
    ensure_search_path(db, ctx.tenant.schema_name)
    try:
        delete_document(db=db, document_id=document_id)
    except DocumentNotFoundError:
        raise HTTPException(status_code=404, detail="Document not found")

    return Response(status_code=status.HTTP_204_NO_CONTENT)
