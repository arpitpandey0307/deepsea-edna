"""
File upload API endpoints
"""
import logging
import re
import uuid
from pathlib import Path
from typing import List

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import require_permission
from app.core.config import settings
from app.core.security import Permission
from app.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_EXTENSIONS = ('.fasta', '.fastq', '.fa', '.fq', '.fas', '.fna')
CHUNK_SIZE = 1024 * 1024
FILE_ID_PATTERN = re.compile(r"^[0-9a-f-]{36}_[\w.\- ()]+$")


def user_upload_dir(user: User) -> Path:
    path = Path(settings.UPLOAD_DIR) / str(user.id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_upload(user: User, file_id: str) -> Path:
    """Map an opaque file ID from /upload back to a file in the user's own folder"""
    if not FILE_ID_PATTERN.match(file_id):
        raise HTTPException(status_code=400, detail=f"Invalid file reference: {file_id}")
    path = user_upload_dir(user) / file_id
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Uploaded file not found: {file_id}")
    return path


@router.post("/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    user: User = Depends(require_permission(Permission.PREDICT_BATCH)),
):
    """
    Upload eDNA sequence files; returns file IDs to pass to /analyze
    """
    upload_dir = user_upload_dir(user)
    uploaded_files = []

    for file in files:
        original_name = Path(file.filename or "").name
        if not original_name.lower().endswith(ALLOWED_EXTENSIONS):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file format: {original_name}. Accepted formats: {', '.join(ALLOWED_EXTENSIONS)}"
            )

        safe_name = re.sub(r"[^\w.\- ()]", "_", original_name)
        file_id = f"{uuid.uuid4()}_{safe_name}"
        file_path = upload_dir / file_id
        size = 0

        try:
            # Stream to disk so large files are never held in memory
            async with aiofiles.open(file_path, 'wb') as f:
                while chunk := await file.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > settings.MAX_UPLOAD_SIZE:
                        raise HTTPException(
                            status_code=413,
                            detail=f"File too large: {original_name}. Maximum size: {settings.MAX_UPLOAD_SIZE // (1024 * 1024)}MB"
                        )
                    await f.write(chunk)
        except Exception as e:
            file_path.unlink(missing_ok=True)
            if isinstance(e, HTTPException):
                raise
            logger.error(f"Error uploading {original_name}: {e}")
            raise HTTPException(status_code=500, detail="Upload failed")

        uploaded_files.append({'filename': original_name, 'path': file_id, 'size': size})
        logger.info(f"User {user.id} uploaded {original_name} ({size} bytes)")

    return {
        'message': f'Successfully uploaded {len(uploaded_files)} files',
        'files': uploaded_files,
        'file_paths': [f['path'] for f in uploaded_files],  # opaque IDs for /analyze
    }
