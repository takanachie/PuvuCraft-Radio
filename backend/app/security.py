from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import tempfile
import threading
import time
from collections import OrderedDict, deque
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request, Response
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session, joinedload

from .config import Settings
from .errors import ApiError
from .models import LoginSession, User, utcnow

logger = logging.getLogger(__name__)


def normalize_identity(value: str) -> str:
    return value.strip().casefold()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class RateLimiter:
    def __init__(self, max_keys: int = 10_000) -> None:
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._max_keys = max_keys
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events.pop(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                self._events[key] = events
                raise ApiError(429, "rate_limited", "操作过于频繁，请稍后重试")
            events.append(now)
            self._events[key] = events
            while len(self._events) > self._max_keys:
                self._events.popitem(last=False)


class BootstrapManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.paths.bootstrap_token_file
        self.creation_lock = threading.Lock()

    def synchronize(self, has_admin: bool) -> None:
        if has_admin:
            self.remove_token()
            return
        if self.path.exists():
            try:
                if len(self.path.read_text(encoding="utf-8").strip()) >= 20:
                    return
            except OSError:
                pass
            self.path.unlink(missing_ok=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(36)
        fd, temporary_name = tempfile.mkstemp(prefix=".bootstrap-", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, f"{token}\n".encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temporary_name, self.path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        directory_fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        logger.warning("Initial administrator setup is required; token file: %s", self.path)

    def verify(self, supplied: str) -> bool:
        try:
            expected = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        return bool(expected) and hmac.compare_digest(expected, supplied)

    def remove_token(self) -> None:
        with suppress(FileNotFoundError):
            self.path.unlink()


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.passwords = PasswordHasher()
        self._dummy_password_hash = self.passwords.hash(secrets.token_urlsafe(24))

    def hash_session_token(self, token: str) -> str:
        return hmac.new(
            self.settings.auth.secret_key.encode("utf-8"),
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def validate_password_policy(self, password: str) -> None:
        policy = self.settings.auth.password_policy
        if not policy.min_length <= len(password) <= policy.max_length:
            raise ApiError(
                422,
                "invalid_password",
                f"密码长度必须为 {policy.min_length}-{policy.max_length} 个字符",
            )

    def hash_password(self, password: str) -> str:
        self.validate_password_policy(password)
        return self.passwords.hash(password)

    def verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return self.passwords.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def verify_login_password(self, password_hash: str | None, password: str) -> bool:
        return self.verify_password(password_hash or self._dummy_password_hash, password)

    def create_session(self, db: Session, user: User) -> tuple[str, str, LoginSession]:
        raw_token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        now = utcnow()
        session = LoginSession(
            token_hash=self.hash_session_token(raw_token),
            csrf_hash=self.hash_session_token(csrf_token),
            user=user,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=self.settings.auth.session.ttl_hours),
        )
        active_sessions = list(
            db.scalars(
                select(LoginSession)
                .where(LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None))
                .order_by(LoginSession.created_at.desc())
            ).all()
        )
        for stale_session in active_sessions[9:]:
            stale_session.revoked_at = now
        db.add(session)
        return raw_token, csrf_token, session

    def cleanup_sessions(self, db: Session) -> None:
        now = utcnow()
        db.execute(
            delete(LoginSession).where(
                or_(
                    LoginSession.expires_at < now,
                    LoginSession.revoked_at < now - timedelta(days=7),
                )
            )
        )

    def set_session_cookies(self, response: Response, raw_token: str, csrf_token: str) -> None:
        session_config = self.settings.auth.session
        max_age = session_config.ttl_hours * 3600
        response.set_cookie(
            session_config.cookie_name,
            raw_token,
            max_age=max_age,
            httponly=True,
            secure=session_config.secure_cookie,
            samesite=session_config.same_site,
            path="/",
        )
        response.set_cookie(
            self.settings.auth.csrf.cookie_name,
            csrf_token,
            max_age=max_age,
            httponly=False,
            secure=session_config.secure_cookie,
            samesite=session_config.same_site,
            path="/",
        )

    def clear_session_cookies(self, response: Response) -> None:
        session_config = self.settings.auth.session
        response.delete_cookie(
            session_config.cookie_name,
            path="/",
            secure=session_config.secure_cookie,
            httponly=True,
            samesite=session_config.same_site,
        )
        response.delete_cookie(
            self.settings.auth.csrf.cookie_name,
            path="/",
            secure=session_config.secure_cookie,
            httponly=False,
            samesite=session_config.same_site,
        )

    def authenticate_request(self, request: Request, db: Session) -> LoginSession:
        raw_token = request.cookies.get(self.settings.auth.session.cookie_name)
        if not raw_token:
            raise ApiError(401, "authentication_required", "请先登录")
        login_session = db.scalar(
            select(LoginSession)
            .options(joinedload(LoginSession.user))
            .where(LoginSession.token_hash == self.hash_session_token(raw_token))
        )
        now = utcnow()
        if (
            login_session is None
            or login_session.revoked_at is not None
            or aware_utc(login_session.expires_at) <= now
            or aware_utc(login_session.last_seen_at)
            + timedelta(hours=self.settings.auth.session.idle_timeout_hours)
            <= now
        ):
            raise ApiError(401, "invalid_session", "登录会话已失效")
        if login_session.user.status != "approved":
            raise ApiError(401, "account_unavailable", "账号当前不可用")
        if aware_utc(login_session.last_seen_at) < now - timedelta(minutes=5):
            login_session.last_seen_at = now
            db.commit()
        return login_session

    def verify_csrf(self, request: Request, login_session: LoginSession) -> None:
        if not self.settings.auth.csrf.enabled:
            return
        cookie = request.cookies.get(self.settings.auth.csrf.cookie_name, "")
        header = request.headers.get(self.settings.auth.csrf.header_name, "")
        if not cookie or not header or not hmac.compare_digest(cookie, header):
            raise ApiError(403, "csrf_failed", "请求安全令牌无效")
        if not hmac.compare_digest(self.hash_session_token(cookie), login_session.csrf_hash):
            raise ApiError(403, "csrf_failed", "请求安全令牌无效")

    def revoke_user_sessions(self, db: Session, user_id: int) -> None:
        db.execute(
            update(LoginSession)
            .where(LoginSession.user_id == user_id, LoginSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
