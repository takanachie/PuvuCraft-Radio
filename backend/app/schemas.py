from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-.]{3,32}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SetupInput(ApiModel):
    token: str = Field(min_length=20, max_length=256)
    username: str
    email: EmailStr
    password: str = Field(max_length=1024)

    @field_validator("username")
    @classmethod
    def username_format(cls, value: str) -> str:
        if not USERNAME_RE.fullmatch(value):
            raise ValueError("用户名需为 3-32 位字母、数字、点、横线或下划线")
        return value


class RegistrationInput(ApiModel):
    username: str
    email: EmailStr
    password: str = Field(max_length=1024)

    @field_validator("username")
    @classmethod
    def username_format(cls, value: str) -> str:
        if not USERNAME_RE.fullmatch(value):
            raise ValueError("用户名需为 3-32 位字母、数字、点、横线或下划线")
        return value


class LoginInput(ApiModel):
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class UserUpdate(ApiModel):
    status: Literal["pending", "approved", "rejected", "disabled"]


class ChannelCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=4000)
    enabled: bool = True
    playback_mode: Literal["sequential", "shuffle"] = "sequential"
    display_order: int = Field(default=1, ge=0, le=10000)

    @field_validator("slug")
    @classmethod
    def slug_format(cls, value: str) -> str:
        if not SLUG_RE.fullmatch(value):
            raise ValueError("slug 只能包含小写字母、数字和单个连字符")
        return value


class ChannelUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None
    playback_mode: Literal["sequential", "shuffle"] | None = None
    display_order: int | None = Field(default=None, ge=0, le=10000)

    @field_validator("slug")
    @classmethod
    def slug_format(cls, value: str | None) -> str | None:
        if value is not None and not SLUG_RE.fullmatch(value):
            raise ValueError("slug 只能包含小写字母、数字和单个连字符")
        return value


class TrackUpdate(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    artist: str | None = Field(default=None, max_length=512)
    album: str | None = Field(default=None, max_length=512)
    cover_url: str | None = Field(default=None, max_length=2048)

    @field_validator("cover_url")
    @classmethod
    def safe_cover_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        if not (value.startswith("https://") or value.startswith("/api/covers/")):
            raise ValueError("封面 URL 必须使用 HTTPS 或本站封面路径")
        return value


class PlaylistAdd(ApiModel):
    track_id: int = Field(gt=0)


class PlaylistItemUpdate(ApiModel):
    position: int | None = Field(default=None, ge=0)


class PlaylistReorder(ApiModel):
    item_ids: list[int] = Field(min_length=1)

    @field_validator("item_ids")
    @classmethod
    def no_duplicates(cls, values: list[int]) -> list[int]:
        if len(values) != len(set(values)):
            raise ValueError("排序列表不能包含重复项目")
        return values
