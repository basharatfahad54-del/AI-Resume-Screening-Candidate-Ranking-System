"""Secure, non-public file storage for uploaded resumes and job descriptions.

Security properties (PRD section 25)
------------------------------------
* Files are stored **outside** the API's static mount and are never served
  directly. The only way to read a document is through an authorised endpoint
  that re-checks ownership/role.
* The persisted filename is a random 32 hex character token plus a whitelisted
  suffix. The client supplied filename is kept in the database for display only
  and is *never* used to build a path.
* Extension and size are validated before a single byte is written.
* Writes are atomic (``tmp`` file + ``os.replace``) so a crashed upload cannot
  leave a half-written document that later looks parseable.
* Directory layout is flat per mount point; path traversal is additionally
  blocked by resolving and verifying the final path stays under the root.
"""

from __future__ import annotations

import os
import secrets
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_CHUNK = 1024 * 1024


class StorageError(Exception):
    """Raised when an upload violates the storage policy."""


@dataclass(frozen=True, slots=True)
class StoredFile:
    relative_path: str
    absolute_path: Path
    size_bytes: int
    original_filename: str
    extension: str


def ensure_storage_dirs() -> None:
    """Create the storage tree. Safe to call repeatedly."""
    settings.resumes_dir.mkdir(parents=True, exist_ok=True)
    (settings.storage_dir / "jobs").mkdir(parents=True, exist_ok=True)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def normalize_extension(filename: str) -> str:
    """Return a lowercase, whitelisted extension (``""`` when not allowed)."""
    suffix = Path(unicodedata.normalize("NFKD", filename or "")).suffix.lower()
    allowed = {ext.lower() for ext in settings.allowed_resume_extensions}
    return suffix if suffix in allowed else ""


def _resolve(root: Path, relative_path: str) -> Path:
    """Resolve ``relative_path`` under ``root``, refusing traversal attempts."""
    candidate = (root / relative_path).resolve()
    root_resolved = root.resolve()
    if not candidate.is_relative_to(root_resolved):
        raise StorageError("Resolved path escapes the storage root")
    return candidate


def save_upload(
    content: bytes,
    original_filename: str,
    *,
    kind: str = "resumes",
) -> StoredFile:
    """Persist ``content`` and return its metadata.

    Raises ``StorageError`` for a disallowed extension, an empty payload or a
    payload larger than ``settings.max_upload_size_mb``.
    """
    extension = normalize_extension(original_filename)
    if not extension:
        raise StorageError(
            f"Unsupported file type. Allowed: {', '.join(sorted(settings.allowed_resume_extensions))}"
        )
    if not content:
        raise StorageError("Uploaded file is empty")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise StorageError(
            f"File is too large ({human_size(len(content))}). "
            f"Maximum allowed is {settings.max_upload_size_mb} MB."
        )

    root = settings.resumes_dir if kind == "resumes" else settings.storage_dir / "jobs"
    root.mkdir(parents=True, exist_ok=True)

    # Partition by upload month to keep directories manageable at scale.
    now = datetime.now(timezone.utc)
    shard = root / f"{now:%Y%m}"
    shard.mkdir(parents=True, exist_ok=True)

    token = secrets.token_hex(16)
    relative_path = f"{now:%Y%m}/{token}{extension}"
    target = _resolve(root, relative_path)
    temporary = target.with_suffix(target.suffix + ".part")

    try:
        with open(temporary, "wb") as handle:
            for start in range(0, len(content), _CHUNK):
                handle.write(content[start : start + _CHUNK])
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError as exc:  # pragma: no cover - filesystem failure
        temporary.unlink(missing_ok=True)
        logger.exception("Failed to persist upload %s", relative_path)
        raise StorageError("Could not persist the uploaded file") from exc

    logger.info(
        "Stored %s upload size=%s original=%s",
        kind,
        human_size(len(content)),
        original_filename,
    )
    return StoredFile(
        relative_path=relative_path,
        absolute_path=target,
        size_bytes=len(content),
        original_filename=original_filename[:300],
        extension=extension,
    )


def stored_path(relative_path: str | None, *, kind: str = "resumes") -> Path | None:
    """Map a stored relative path back to an absolute path, or ``None``."""
    if not relative_path:
        return None
    root = settings.resumes_dir if kind == "resumes" else settings.storage_dir / "jobs"
    try:
        return _resolve(root, relative_path)
    except StorageError:
        logger.warning("Rejected traversal attempt for stored path %r", relative_path)
        return None


def delete_stored_file(relative_path: str | None, *, kind: str = "resumes") -> bool:
    """Delete a stored document. Returns ``True`` when a file was removed."""
    path = stored_path(relative_path, kind=kind)
    if path is None or not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:  # pragma: no cover - filesystem failure
        logger.exception("Failed to delete stored file %s", relative_path)
        return False
    return True


def read_stored_file(relative_path: str | None, *, kind: str = "resumes") -> bytes:
    path = stored_path(relative_path, kind=kind)
    if path is None or not path.is_file():
        raise StorageError("Stored document not found")
    return path.read_bytes()


def purge_expired_uploads(
    retention_days: int | None = None,
    protected_names: set[str] | None = None,
) -> int:
    """Delete stored documents older than the retention window.

    Returns the number of files removed. Used by the retention job and the
    ``/api/admin/purge`` endpoint so a candidate can be forgotten on schedule
    (PRD section 26 - data retention).

    ``protected_names`` holds the file names still referenced by a candidate or
    job row. Those are skipped even when they are past the window: deleting them
    would leave a database row pointing at a missing document, which breaks the
    profile a recruiter is looking at. Retention is about forgetting data, not
    about breaking live records.
    """
    days = retention_days if retention_days is not None else settings.retention_days
    if days <= 0:
        return 0
    protected = protected_names or set()
    cutoff = time.time() - days * 86400
    removed = 0
    skipped = 0
    for root in (settings.resumes_dir, settings.storage_dir / "jobs"):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.name in protected:
                skipped += 1
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:  # pragma: no cover
                continue
    if skipped:
        logger.info(
            "Retention purge kept %s file(s) still referenced by a record", skipped
        )
    if removed:
        logger.info("Retention purge removed %s file(s) older than %s day(s)", removed, days)
    return removed
