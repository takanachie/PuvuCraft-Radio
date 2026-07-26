from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigError(RuntimeError):
    pass


_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppConfig(StrictModel):
    name: str
    environment: Literal["development", "test", "production"]
    timezone: str
    public_base_url: str


class ServerConfig(StrictModel):
    host: str
    port: int = Field(ge=1, le=65535)
    workers: int = Field(default=1, ge=1)
    proxy_headers: bool = True
    trusted_proxies: list[str] = Field(default_factory=list)

    @field_validator("workers")
    @classmethod
    def single_worker(cls, value: int) -> int:
        if value != 1:
            raise ValueError("the playback supervisor requires exactly one API worker")
        return value


class PathsConfig(StrictModel):
    data_dir: Path
    cover_dir: Path
    hls_dir: Path
    log_dir: Path
    bootstrap_token_file: Path


class DatabaseConfig(StrictModel):
    url: str
    sqlite_wal: bool = True
    busy_timeout_ms: int = Field(default=5000, ge=0)


class RegistrationConfig(StrictModel):
    enabled: bool = True
    require_approval: bool = True
    require_email: bool = True
    require_email_verification: bool = False


class PasswordPolicyConfig(StrictModel):
    min_length: int = Field(default=10, ge=8, le=128)
    max_length: int = Field(default=128, ge=8, le=1024)

    @model_validator(mode="after")
    def valid_range(self) -> PasswordPolicyConfig:
        if self.max_length < self.min_length:
            raise ValueError("password max_length must be at least min_length")
        return self


class SessionConfig(StrictModel):
    cookie_name: str = "radio_session"
    ttl_hours: int = Field(default=168, ge=1)
    idle_timeout_hours: int = Field(default=24, ge=1)
    secure_cookie: bool = False
    same_site: Literal["lax", "strict", "none"] = "lax"


class CsrfConfig(StrictModel):
    enabled: bool = True
    cookie_name: str = "radio_csrf"
    header_name: str = "X-CSRF-Token"


class RateLimitsConfig(StrictModel):
    login_per_minute: int = Field(default=10, ge=1)
    register_per_hour: int = Field(default=5, ge=1)
    setup_per_minute: int = Field(default=5, ge=1)


class AuthConfig(StrictModel):
    secret_key: str = Field(min_length=32)
    password_hash: Literal["argon2id"] = "argon2id"
    registration: RegistrationConfig
    password_policy: PasswordPolicyConfig
    session: SessionConfig
    csrf: CsrfConfig
    rate_limits: RateLimitsConfig


class BootstrapConfig(StrictModel):
    mode: Literal["first_visit"] = "first_visit"
    require_one_time_token: bool = True


class MediaMetadataConfig(StrictModel):
    extract_tags: bool = True
    extract_embedded_cover: bool = True
    allow_admin_edit: bool = True


class MediaConfig(StrictModel):
    max_upload_bytes: int = Field(gt=0)
    allowed_extensions: list[str]
    import_directories: list[Path]
    deduplicate_by: Literal["sha256"] = "sha256"
    duplicate_policy: Literal["reuse"] = "reuse"
    missing_file_policy: Literal["mark_unavailable"] = "mark_unavailable"
    metadata: MediaMetadataConfig

    @field_validator("allowed_extensions")
    @classmethod
    def normalize_extensions(cls, values: list[str]) -> list[str]:
        return [value.lower() if value.startswith(".") else f".{value.lower()}" for value in values]


class UploadsConfig(StrictModel):
    temp_dir: Path
    queue_limit: Literal[10] = 10
    max_concurrent: int = Field(default=3, ge=2, le=32)
    ready_lease_seconds: int = Field(default=120, ge=15, le=3600)
    heartbeat_interval_seconds: int = Field(default=5, ge=1, le=60)
    heartbeat_timeout_seconds: int = Field(default=15, ge=3, le=300)
    progress_checkpoint_bytes: int = Field(default=4 * 1024 * 1024, ge=64 * 1024)
    history_limit: int = Field(default=100, ge=10, le=1000)

    @model_validator(mode="after")
    def valid_queue(self) -> UploadsConfig:
        if self.max_concurrent > self.queue_limit:
            raise ValueError("uploads.max_concurrent cannot exceed uploads.queue_limit")
        if self.heartbeat_timeout_seconds <= self.heartbeat_interval_seconds:
            raise ValueError(
                "uploads.heartbeat_timeout_seconds must exceed heartbeat_interval_seconds"
            )
        return self


class StorageLocationConfig(StrictModel):
    id: str
    root: Path
    priority: int = 0
    max_usage_percent: float = Field(gt=0, le=100)
    enabled: bool = True
    create_if_missing: bool = False

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not _SLUG_PATTERN.fullmatch(value):
            raise ValueError("storage location id contains unsupported characters")
        return value


class StorageConfig(StrictModel):
    locations: list[StorageLocationConfig]

    @model_validator(mode="after")
    def valid_locations(self) -> StorageConfig:
        ids = [location.id for location in self.locations]
        if len(ids) != len(set(ids)):
            raise ValueError("storage location ids must be unique")
        if not any(location.enabled for location in self.locations):
            raise ValueError("at least one storage location must be enabled")
        return self


class RestartConfig(StrictModel):
    initial_delay_seconds: float = Field(default=1, ge=0)
    max_delay_seconds: float = Field(default=30, gt=0)
    max_failures_before_offline: int = Field(default=10, ge=1)


class FfmpegConfig(StrictModel):
    binary: Path
    ffprobe_binary: Path
    log_level: str = "warning"
    restart: RestartConfig


class StreamOutputConfig(StrictModel):
    codec: Literal["aac"] = "aac"
    profile: str = "aac_low"
    bitrate: str = "192k"
    sample_rate: int = Field(default=48000, ge=8000)
    channels: int = Field(default=2, ge=1, le=8)
    sample_bits: Literal[32] = 32


class HlsConfig(StrictModel):
    segment_container: Literal["mpegts"] = "mpegts"
    segment_duration_seconds: int = Field(default=4, ge=1)
    playlist_segments: int = Field(default=6, ge=3)
    delete_old_segments: bool = True
    delete_threshold: int = Field(default=2, ge=1)


class PlaybackConfig(StrictModel):
    default_mode: Literal["sequential", "shuffle"] = "sequential"
    allowed_modes: list[Literal["sequential", "shuffle"]]
    loop: bool = True
    transition: Literal["direct"] = "direct"
    recovery: Literal["timeline"] = "timeline"
    state_checkpoint_seconds: int = Field(default=5, ge=1)


class ProcessControlConfig(StrictModel):
    shutdown_timeout_seconds: int = Field(default=10, ge=1)
    stale_output_cleanup: bool = True


class StreamingConfig(StrictModel):
    always_on: bool = True
    require_authenticated_session: bool = True
    output: StreamOutputConfig
    hls: HlsConfig
    playback: PlaybackConfig
    process_control: ProcessControlConfig


class StreamAccessConfig(StrictModel):
    nginx_auth_request_path: str
    authorization_cache_seconds: int = Field(default=5, ge=0)
    allow_anonymous: bool = False


class EventsConfig(StrictModel):
    transport: Literal["sse"] = "sse"
    heartbeat_seconds: int = Field(default=15, ge=1)
    playback_position_interval_seconds: int = Field(default=2, ge=1)


class FrontendConfig(StrictModel):
    theme: str
    default_locale: str
    show_full_playlist: bool = True
    show_seek_control: bool = False
    return_to_live_on_resume: bool = True


class SeedChannelConfig(StrictModel):
    name: str
    slug: str
    description: str = ""
    enabled: bool = True
    playback_mode: Literal["sequential", "shuffle"] = "sequential"
    display_order: int = 1

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        if not _SLUG_PATTERN.fullmatch(value):
            raise ValueError("seed channel slug contains unsupported characters")
        return value


class SeedConfig(StrictModel):
    channels: list[SeedChannelConfig] = Field(default_factory=list)


class LoggingConfig(StrictModel):
    level: str = "INFO"
    format: Literal["json", "text"] = "json"
    file: Path
    max_bytes: int = Field(gt=0)
    backup_count: int = Field(ge=0)


class Settings(StrictModel):
    version: Literal[1]
    app: AppConfig
    server: ServerConfig
    paths: PathsConfig
    database: DatabaseConfig
    auth: AuthConfig
    bootstrap: BootstrapConfig
    media: MediaConfig
    uploads: UploadsConfig
    storage: StorageConfig
    ffmpeg: FfmpegConfig
    streaming: StreamingConfig
    stream_access: StreamAccessConfig
    events: EventsConfig
    frontend: FrontendConfig
    seed: SeedConfig
    logging: LoggingConfig
    config_path: Path = Field(default=Path("config.yaml"), exclude=True)

    @model_validator(mode="after")
    def production_security(self) -> Settings:
        if not self.bootstrap.require_one_time_token:
            raise ValueError("v1 requires a one-time bootstrap token")
        if not self.auth.registration.require_approval:
            raise ValueError("v1 requires administrator approval for registrations")
        if not self.auth.csrf.enabled:
            raise ValueError("v1 requires CSRF protection")
        if (
            self.auth.csrf.cookie_name != "radio_csrf"
            or self.auth.csrf.header_name.lower() != "x-csrf-token"
        ):
            raise ValueError("v1 frontend requires the standard CSRF cookie and header names")
        if self.auth.session.same_site != "lax":
            raise ValueError("v1 same-origin deployment requires SameSite=Lax")
        if self.app.environment == "production":
            if not self.auth.session.secure_cookie:
                raise ValueError("production requires auth.session.secure_cookie=true")
            if not self.app.public_base_url.startswith("https://"):
                raise ValueError("production public_base_url must use HTTPS")
        if self.stream_access.allow_anonymous or not self.streaming.require_authenticated_session:
            raise ValueError("anonymous streaming is outside the v1 specification")
        return self


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_environment(value: object) -> object:
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        try:
            return os.environ[name]
        except KeyError as exc:
            raise ConfigError(f"required environment variable {name} is not set") from exc

    return _ENV_PATTERN.sub(replace, value)


def _absolute_path(base: Path, raw: str | Path) -> str:
    path = Path(raw).expanduser()
    return str(path if path.is_absolute() else (base / path).resolve())


def _resolve_paths(data: dict[str, object], base: Path) -> None:
    paths = data.get("paths")
    if isinstance(paths, dict):
        for key, value in list(paths.items()):
            if isinstance(value, (str, Path)):
                paths[key] = _absolute_path(base, value)

    media = data.get("media")
    if isinstance(media, dict) and isinstance(media.get("import_directories"), list):
        media["import_directories"] = [
            _absolute_path(base, value) for value in media["import_directories"]
        ]

    uploads = data.get("uploads")
    if isinstance(uploads, dict) and isinstance(uploads.get("temp_dir"), (str, Path)):
        uploads["temp_dir"] = _absolute_path(base, uploads["temp_dir"])

    storage = data.get("storage")
    if isinstance(storage, dict) and isinstance(storage.get("locations"), list):
        for location in storage["locations"]:
            if isinstance(location, dict) and isinstance(location.get("root"), (str, Path)):
                location["root"] = _absolute_path(base, location["root"])

    logging = data.get("logging")
    if isinstance(logging, dict) and isinstance(logging.get("file"), (str, Path)):
        logging["file"] = _absolute_path(base, logging["file"])

    database = data.get("database")
    if isinstance(database, dict):
        url = database.get("url")
        if isinstance(url, str) and url.startswith("sqlite:///"):
            target = url.removeprefix("sqlite:///")
            database["url"] = f"sqlite:///{_absolute_path(base, target)}"


def load_settings(path: str | Path | None = None) -> Settings:
    selected = Path(path or os.getenv("RADIO_CONFIG", "config.yaml")).expanduser()
    if not selected.is_absolute():
        selected = (Path.cwd() / selected).resolve()
    if not selected.exists() and selected.name == "config.yaml":
        example = selected.with_name("config.example.yaml")
        if example.exists():
            selected = example
    if not selected.exists():
        raise ConfigError(f"configuration file not found: {selected}")

    try:
        raw = yaml.safe_load(selected.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read configuration: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a mapping")

    expanded = _expand_environment(raw)
    assert isinstance(expanded, dict)
    ffmpeg = expanded.get("ffmpeg")
    if isinstance(ffmpeg, dict):
        if binary := os.getenv("RADIO_FFMPEG_BINARY"):
            ffmpeg["binary"] = binary
        if probe_binary := os.getenv("RADIO_FFPROBE_BINARY"):
            ffmpeg["ffprobe_binary"] = probe_binary
    _resolve_paths(expanded, selected.parent)
    expanded["config_path"] = selected
    return Settings.model_validate(expanded)
