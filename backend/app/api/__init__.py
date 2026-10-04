"""API layer: shared dependencies and versioned routers."""

from app.api.deps import (
    AdminUser,
    CurrentUser,
    Pagination,
    PaginationDep,
    RecruiterUser,
    SessionDep,
    client_ip,
)

__all__ = [
    "AdminUser",
    "CurrentUser",
    "Pagination",
    "PaginationDep",
    "RecruiterUser",
    "SessionDep",
    "client_ip",
]