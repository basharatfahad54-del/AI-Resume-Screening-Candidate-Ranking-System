"""Authentication endpoints (PRD section 27).

Refresh tokens are opaque, single-use and stored hashed; access tokens are short
lived. Every outcome - success or failure - is audited.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentUser, SessionDep, client_ip, user_agent
from app.models import User, UserRole
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    PasswordChangeRequest,
    RefreshRequest,
    UserRead,
    UserRegisterRequest,
)
from app.services import audit as audit_service
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _client(request: Request) -> dict[str, str | None]:
    return {"ip_address": client_ip(request), "user_agent": user_agent(request)}


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a recruiter account",
)
async def register(payload: UserRegisterRequest, request: Request, session: SessionDep) -> AuthResponse:
    """Self-service signup.

    The submitted ``role`` is ignored and always forced to ``recruiter``.
    Accepting a caller-supplied role would let anyone create themselves an
    administrator; role assignment lives behind ``POST /admin/users``.
    """
    existing = await auth_service.get_user_by_email(session, payload.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That email is already registered."
        )

    user = await auth_service.create_user(
        session,
        name=payload.name,
        email=payload.email,
        password=payload.password,
        role=UserRole.recruiter,
    )
    pair = auth_service.issue_tokens(user)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_REGISTER,
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        # The requested role is recorded to make a probing attempt visible, but
        # it was not honoured.
        detail={"role": user.role.value, "requested_role": payload.role.value},
        **_client(request),
    )
    await session.commit()
    return AuthResponse(user=UserRead.model_validate(user), tokens=pair)


@router.post("/login", response_model=AuthResponse, summary="Exchange credentials for tokens")
async def login(payload: LoginRequest, request: Request, session: SessionDep) -> AuthResponse:
    try:
        user, pair = await auth_service.authenticate(
            session,
            email=payload.email,
            password=payload.password,
            **_client(request),
        )
    except auth_service.AuthError as exc:
        # The message is deliberately identical for unknown email and wrong
        # password so the endpoint cannot be used to enumerate accounts.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password."
        ) from exc

    await session.commit()
    return AuthResponse(user=UserRead.model_validate(user), tokens=pair)


@router.post("/refresh", response_model=AuthResponse, summary="Rotate a refresh token")
async def refresh(payload: RefreshRequest, request: Request, session: SessionDep) -> AuthResponse:
    try:
        user, pair = await auth_service.refresh_access_token(session, payload.refresh_token)
    except auth_service.AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    await audit_service.audit(
        session,
        action=audit_service.ACTION_TOKEN_REFRESH,
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        **_client(request),
    )
    await session.commit()
    return AuthResponse(user=UserRead.model_validate(user), tokens=pair)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Revoke one or all sessions",
)
async def logout(
    request: Request, session: SessionDep, user: CurrentUser, payload: LogoutRequest | None = None
) -> None:
    """End a session.

    A ``refresh_token`` in the body ends that one session; an empty body revokes
    every refresh token the user holds ("sign out everywhere"). The access token
    is deliberately not accepted here - it is stateless and short lived, so
    revoking it would be theatre; the refresh token is what actually confers
    lasting access. ``logout()`` writes its own audit row, so this handler does
    not add a second, duplicate one.
    """
    await auth_service.logout(
        session, user.id, token=payload.refresh_token if payload else None
    )
    await session.commit()


@router.get("/me", response_model=UserRead, summary="The authenticated user")
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Change your own password",
)
async def change_password(
    payload: PasswordChangeRequest, request: Request, session: SessionDep, user: CurrentUser
) -> None:
    """Change the caller's password, revoking every other session."""
    if not auth_service.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="The current password is incorrect."
        )

    user.password_hash = auth_service.hash_password(payload.new_password)
    # Every existing refresh token is invalidated, so a stolen token cannot
    # outlive the password change.
    await auth_service.revoke_all_tokens(session, user.id)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_USER_UPDATE,
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        detail={"change": "password"},
        **_client(request),
    )
    await session.commit()


@router.post(
    "/bootstrap-admin",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create the first administrator (bootstrap only)",
)
async def bootstrap_admin(session: SessionDep) -> User:
    user = await auth_service.bootstrap_admin(session)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An administrator already exists; register through the normal flow.",
        )
    await session.commit()
    return UserRead.model_validate(user)