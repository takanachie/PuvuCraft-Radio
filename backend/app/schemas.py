from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-.]{3,32}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
UPLOAD_CLIENT_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def validate_library_name(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("音乐库名称不能包含控制字符")
    if "/" in value or "\\" in value:
        raise ValueError("音乐库名称不能包含路径分隔符")
    return value


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


class UserRoleUpdate(ApiModel):
    role: Literal["admin"]


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


class TrackLibraryBatchMove(ApiModel):
    source_library: str = Field(min_length=1, max_length=80)
    target_library: str = Field(min_length=1, max_length=80)
    track_ids: list[int] = Field(min_length=1, max_length=1000)

    @field_validator("source_library", "target_library")
    @classmethod
    def library_name_format(cls, value: str) -> str:
        return validate_library_name(value)

    @field_validator("track_ids")
    @classmethod
    def unique_track_ids(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("曲目 ID 无效")
        if len(set(values)) != len(values):
            raise ValueError("曲目 ID 不能重复")
        return values


class MusicLibraryCreate(ApiModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def library_name_format(cls, value: str) -> str:
        return validate_library_name(value)


class MusicLibraryUpdate(ApiModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def library_name_format(cls, value: str) -> str:
        return validate_library_name(value)


class UploadReservationInput(ApiModel):
    client_id: str
    filename: str = Field(min_length=1, max_length=512)
    size_bytes: int = Field(gt=0)
    confirm_similar: bool = False

    @field_validator("client_id")
    @classmethod
    def client_id_format(cls, value: str) -> str:
        if not UPLOAD_CLIENT_RE.fullmatch(value):
            raise ValueError("上传客户端标识无效")
        return value


class UploadPreflightInput(ApiModel):
    filenames: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("filenames")
    @classmethod
    def valid_filenames(cls, values: list[str]) -> list[str]:
        filenames: list[str] = []
        seen: set[str] = set()
        for value in values:
            filename = value.strip()
            if not filename or len(filename) > 512:
                raise ValueError("待检查文件名长度必须为 1-512 个字符")
            if filename not in seen:
                filenames.append(filename)
                seen.add(filename)
        return filenames


class UploadHeartbeatInput(ApiModel):
    client_id: str

    @field_validator("client_id")
    @classmethod
    def client_id_format(cls, value: str) -> str:
        if not UPLOAD_CLIENT_RE.fullmatch(value):
            raise ValueError("上传客户端标识无效")
        return value


class PlaylistAdd(ApiModel):
    track_id: int = Field(gt=0)


class PlaylistBatchAdd(ApiModel):
    track_ids: list[int] = Field(min_length=1)

    @field_validator("track_ids")
    @classmethod
    def valid_track_ids(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("曲目 ID 必须为正整数")
        if len(values) != len(set(values)):
            raise ValueError("批量添加不能包含重复曲目")
        return values


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
