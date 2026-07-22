from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .config import Settings
from .errors import ApiError
from .models import LoginSession, User
from .security import AuthService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    yield from request.app.state.database.session()


def get_auth(request: Request) -> AuthService:
    return request.app.state.auth


def get_login_session(
    request: Request,
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth),
) -> LoginSession:
    return auth.authenticate_request(request, db)


def get_current_user(login_session: LoginSession = Depends(get_login_session)) -> User:
    return login_session.user


def authenticate_once(request: Request) -> int:
    with request.app.state.database.session_factory() as db:
        login_session = request.app.state.auth.authenticate_request(request, db)
        return login_session.id


def require_csrf(
    request: Request,
    login_session: LoginSession = Depends(get_login_session),
    auth: AuthService = Depends(get_auth),
) -> User:
    auth.verify_csrf(request, login_session)
    return login_session.user


def require_admin(user: User = Depends(require_csrf)) -> User:
    if user.role != "admin":
        raise ApiError(403, "admin_required", "需要管理员权限")
    return user


def require_admin_read(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise ApiError(403, "admin_required", "需要管理员权限")
    return user
