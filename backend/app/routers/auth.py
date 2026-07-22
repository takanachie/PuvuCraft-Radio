from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..dependencies import get_auth, get_current_user, get_db, get_login_session, get_settings
from ..errors import ApiError
from ..models import LoginSession, User, utcnow
from ..schemas import LoginInput, RegistrationInput, SetupInput
from ..security import (
    AuthService,
    BootstrapManager,
    RateLimiter,
    normalize_identity,
    token_hash,
)
from ..serializers import user_dict

router = APIRouter(prefix="/api")


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_key(*parts: str) -> str:
    return token_hash("\0".join(parts))


def _validate_unique_user(db: Session, username: str, email: str) -> None:
    username_key = normalize_identity(username)
    email_key = normalize_identity(email)
    existing = db.scalar(
        select(User.id).where(
            or_(
                User.username_normalized == username_key,
                User.email_normalized == email_key,
            )
        )
    )
    if existing:
        raise ApiError(409, "identity_exists", "用户名或邮箱已被使用")


@router.get("/setup/status")
def setup_status(db: Session = Depends(get_db)) -> dict[str, bool]:
    required = not bool(db.scalar(select(User.id).where(User.role == "admin").limit(1)))
    return {"required": required}


@router.post("/setup", status_code=201)
def create_first_admin(
    payload: SetupInput,
    request: Request,
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    limiter: RateLimiter = request.app.state.rate_limiter
    limiter.check(
        _rate_key("setup", _client_key(request)),
        settings.auth.rate_limits.setup_per_minute,
        60,
    )
    bootstrap: BootstrapManager = request.app.state.bootstrap
    with bootstrap.creation_lock:
        if db.scalar(select(User.id).where(User.role == "admin").limit(1)):
            raise ApiError(409, "setup_complete", "初始化已经完成")
        if settings.bootstrap.require_one_time_token and not bootstrap.verify(payload.token):
            raise ApiError(403, "invalid_setup_token", "初始化令牌无效")
        _validate_unique_user(db, payload.username, str(payload.email))
        now = utcnow()
        user = User(
            username=payload.username,
            username_normalized=normalize_identity(payload.username),
            email=str(payload.email),
            email_normalized=normalize_identity(str(payload.email)),
            password_hash=auth.hash_password(payload.password),
            role="admin",
            status="approved",
            approved_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ApiError(409, "setup_race", "初始化已由另一个请求完成") from exc
        db.refresh(user)
        bootstrap.remove_token()
        return {"user": user_dict(user)}


@router.post("/auth/register", status_code=201)
def register(
    payload: RegistrationInput,
    request: Request,
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    if not settings.auth.registration.enabled:
        raise ApiError(403, "registration_disabled", "当前未开放注册")
    if not db.scalar(select(User.id).where(User.role == "admin").limit(1)):
        raise ApiError(503, "setup_required", "管理员尚未完成系统初始化")
    request.app.state.rate_limiter.check(
        _rate_key("register", _client_key(request)),
        settings.auth.rate_limits.register_per_hour,
        3600,
    )
    _validate_unique_user(db, payload.username, str(payload.email))
    now = utcnow()
    user = User(
        username=payload.username,
        username_normalized=normalize_identity(payload.username),
        email=str(payload.email),
        email_normalized=normalize_identity(str(payload.email)),
        password_hash=auth.hash_password(payload.password),
        role="listener",
        status="pending" if settings.auth.registration.require_approval else "approved",
        approved_at=None if settings.auth.registration.require_approval else now,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "identity_exists", "用户名或邮箱已被使用") from exc
    db.refresh(user)
    return {
        "status": "pending_approval" if user.status == "pending" else user.status,
        "message": "注册成功，等待管理员审批" if user.status == "pending" else "注册成功",
        "user": user_dict(user),
    }


@router.post("/auth/login")
def login(
    payload: LoginInput,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    identity = normalize_identity(payload.username)
    limiter: RateLimiter = request.app.state.rate_limiter
    limiter.check(
        _rate_key("login-ip", _client_key(request)),
        settings.auth.rate_limits.login_per_minute,
        60,
    )
    limiter.check(
        _rate_key("login-account", identity),
        settings.auth.rate_limits.login_per_minute,
        60,
    )
    user = db.scalar(
        select(User).where(
            or_(User.username_normalized == identity, User.email_normalized == identity)
        )
    )
    if not auth.verify_login_password(user.password_hash if user else None, payload.password):
        raise ApiError(401, "invalid_credentials", "用户名或密码错误")
    if user.status != "approved":
        raise ApiError(403, "account_not_approved", "账号尚未获准登录")
    raw_token, csrf_token, _session = auth.create_session(db, user)
    user.last_login_at = utcnow()
    user.updated_at = utcnow()
    db.commit()
    auth.set_session_cookies(response, raw_token, csrf_token)
    return {"user": user_dict(user)}


@router.post("/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    login_session: LoginSession = Depends(get_login_session),
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth),
) -> Response:
    auth.verify_csrf(request, login_session)
    login_session.revoked_at = utcnow()
    db.commit()
    auth.clear_session_cookies(response)
    response.status_code = 204
    return response


@router.get("/auth/me")
def current_user(user: User = Depends(get_current_user)) -> dict[str, object]:
    return {"user": user_dict(user)}
