"""Shared utilities: secure file storage, text helpers, pagination."""

from app.utils.pagination import Page, paginate
from app.utils.storage import (
    StorageError,
    delete_stored_file,
    ensure_storage_dirs,
    human_size,
    save_upload,
    stored_path,
)
from app.utils.text import (
    collapse_whitespace,
    mask_email,
    redact_pii,
    snippet,
    titlecase_keywords,
    truncate,
)

__all__ = [
    "Page",
    "StorageError",
    "collapse_whitespace",
    "delete_stored_file",
    "ensure_storage_dirs",
    "human_size",
    "mask_email",
    "paginate",
    "redact_pii",
    "save_upload",
    "snippet",
    "stored_path",
    "titlecase_keywords",
    "truncate",
]
