# app/utils/file_storage.py
import os
import uuid
from pathlib import Path
from typing import Tuple

from app.core.config import get_settings

settings = get_settings()


def get_storage_root() -> Path:
    """
    Returns the absolute path to the file storage root directory.

    By default, this is "<project_root>/uploads", but it can be overridden
    via FILE_STORAGE_ROOT or file_storage_root in settings.
    """
    root = Path(settings.file_storage_root)
    if not root.is_absolute():
        # Make it relative to the current working directory (Backend/)
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_bytes_to_storage(
    data: bytes,
    original_filename: str,
    subdir: str,
) -> str:
    """
    Save a blob of bytes to storage under a subdirectory.

    Returns a **relative storage path** (e.g. "tenant_ab12cd34/<uuid>.pdf")
    which can be stored in the database.

    - `subdir` can be something like "tenant_ab12cd34/patients/<patient_id>".
    """
    storage_root = get_storage_root()
    safe_subdir = subdir.strip().strip("/").replace("\\", "/")

    dir_path = storage_root / safe_subdir
    dir_path.mkdir(parents=True, exist_ok=True)

    ext = Path(original_filename).suffix
    file_id = uuid.uuid4().hex
    filename = f"{file_id}{ext}"
    full_path = dir_path / filename

    with open(full_path, "wb") as f:
        f.write(data)

    # Return path relative to storage_root
    rel_path = os.path.join(safe_subdir, filename).replace("\\", "/")
    return rel_path


def resolve_storage_path(storage_path: str) -> Path:
    """
    Convert a relative storage path (stored in DB) into an absolute filesystem path.
    """
    storage_root = get_storage_root()
    return storage_root / storage_path