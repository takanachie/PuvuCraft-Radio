from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import User, utcnow
from ..security import aware_utc

_TOKEN_PREFIX = "pv1_"
_TOKEN_AAD = [b"puvucraft-radio/player-token/v1"]
_TOKEN_PAYLOAD = struct.Struct(">QQ16s")
_GENERATION_BYTES = 16


class InvalidPlayerToken(ValueError):
    """Raised without a detailed reason so public player routes can fail closed."""


@dataclass(frozen=True)
class PlayerTokenClaims:
    user_id: int
    created_at: datetime
    generation: bytes


@dataclass(frozen=True)
class ValidatedPlayerToken:
    user: User
    claims: PlayerTokenClaims

    @property
    def identity(self) -> tuple[int, bytes]:
        return self.user.id, self.claims.generation


def initialize_player_credential(user: User, now: datetime | None = None) -> None:
    user.player_key_created_at = now or utcnow()
    user.player_key_generation = secrets.token_bytes(_GENERATION_BYTES)


class PlayerTokenService:
    """Issue one deterministic, authenticated opaque token for a user generation."""

    def __init__(self, settings: Settings) -> None:
        self._connect_before = timedelta(days=settings.player_api.connect_before_days)
        secret = settings.auth.secret_key.encode("utf-8")
        key = hmac.new(
            secret,
            b"puvucraft-radio/player-token/aes-siv-key/v1",
            hashlib.sha512,
        ).digest()
        self._cipher = AESSIV(key)

    def connect_before(self, user: User) -> datetime | None:
        if user.player_key_created_at is None:
            return None
        return aware_utc(user.player_key_created_at) + self._connect_before

    def issue(self, user: User) -> str:
        created_at = user.player_key_created_at
        generation = user.player_key_generation
        if created_at is None or generation is None or len(generation) != _GENERATION_BYTES:
            raise InvalidPlayerToken
        payload = _TOKEN_PAYLOAD.pack(
            user.id,
            int(aware_utc(created_at).timestamp()),
            generation,
        )
        ciphertext = self._cipher.encrypt(payload, _TOKEN_AAD)
        encoded = base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode("ascii")
        return f"{_TOKEN_PREFIX}{encoded}"

    def decode(self, token: str) -> PlayerTokenClaims:
        if not token.startswith(_TOKEN_PREFIX) or not 64 <= len(token) <= 128:
            raise InvalidPlayerToken
        encoded = token.removeprefix(_TOKEN_PREFIX)
        padding = "=" * (-len(encoded) % 4)
        try:
            ciphertext = base64.b64decode(
                f"{encoded}{padding}",
                altchars=b"-_",
                validate=True,
            )
            payload = self._cipher.decrypt(ciphertext, _TOKEN_AAD)
            user_id, created_timestamp, generation = _TOKEN_PAYLOAD.unpack(payload)
            created_at = datetime.fromtimestamp(created_timestamp, tz=UTC)
        except (InvalidTag, ValueError, binascii.Error, struct.error, OverflowError):
            raise InvalidPlayerToken from None
        if user_id <= 0 or len(generation) != _GENERATION_BYTES:
            raise InvalidPlayerToken
        return PlayerTokenClaims(
            user_id=user_id,
            created_at=created_at,
            generation=generation,
        )

    def validate(
        self,
        db: Session,
        token: str,
        *,
        now: datetime | None = None,
    ) -> ValidatedPlayerToken:
        claims = self.decode(token)
        user = db.get(User, claims.user_id)
        if (
            user is None
            or user.status != "approved"
            or user.player_key_created_at is None
            or user.player_key_generation is None
            or int(aware_utc(user.player_key_created_at).timestamp())
            != int(claims.created_at.timestamp())
            or not hmac.compare_digest(user.player_key_generation, claims.generation)
        ):
            raise InvalidPlayerToken
        if aware_utc(now or utcnow()) > (
            aware_utc(user.player_key_created_at) + self._connect_before
        ):
            raise InvalidPlayerToken
        return ValidatedPlayerToken(user=user, claims=claims)
