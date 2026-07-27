from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .config import Settings, load_settings
from .database import Database
from .errors import install_error_handlers
from .routers import admin, auth, channels, uploads
from .security import AuthService, BootstrapManager, RateLimiter
from .services.listeners import ListenerRegistry
from .services.media import MediaService
from .services.playback import PlaybackManager
from .services.storage import StorageManager
from .services.uploads import UploadManager


def configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.logging.level.upper(), logging.INFO))
    if settings.app.environment == "test":
        return
    settings.logging.file.parent.mkdir(parents=True, exist_ok=True)
    if not any(
        isinstance(handler, RotatingFileHandler)
        and Path(handler.baseFilename) == settings.logging.file
        for handler in root.handlers
    ):
        handler = RotatingFileHandler(
            settings.logging.file,
            maxBytes=settings.logging.max_bytes,
            backupCount=settings.logging.backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"
                if settings.logging.format == "text"
                else '{"time":"%(asctime)s","level":"%(levelname)s",'
                '"logger":"%(name)s","message":"%(message)s"}'
            )
        )
        root.addHandler(handler)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings)
    database = Database(settings)
    auth_service = AuthService(settings)
    bootstrap = BootstrapManager(settings)
    storage = StorageManager(settings)
    media = MediaService(settings, storage)
    listeners = ListenerRegistry(settings.stream_access.listener_timeout_seconds)
    playback = PlaybackManager(settings, database, storage, listeners)
    upload_manager = UploadManager(database, media, storage)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        for path in (
            settings.paths.data_dir,
            settings.paths.cover_dir,
            settings.paths.hls_dir,
            settings.paths.log_dir,
            *settings.media.import_directories,
        ):
            path.mkdir(parents=True, exist_ok=True)
        database.initialize()
        with database.session_factory.begin() as db:
            auth_service.cleanup_sessions(db)
        bootstrap.synchronize(database.has_admin())
        await upload_manager.start()
        await playback.start()
        try:
            yield
        finally:
            await playback.stop()
            await upload_manager.stop()
            database.close()

    app = FastAPI(
        title=settings.app.name,
        version="0.1.0",
        docs_url="/api/docs" if settings.app.environment != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.app.environment != "production" else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.auth = auth_service
    app.state.bootstrap = bootstrap
    app.state.rate_limiter = RateLimiter()
    app.state.storage = storage
    app.state.media = media
    app.state.listeners = listeners
    app.state.playback = playback
    app.state.uploads = upload_manager

    install_error_handlers(app)
    app.include_router(auth.router)
    app.include_router(channels.router)
    app.include_router(admin.router)
    app.include_router(uploads.router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app.name}

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"

    @app.get("/{spa_path:path}", include_in_schema=False)
    def frontend_fallback(spa_path: str):
        if spa_path.startswith(("api/", "hls/")):
            return JSONResponse(
                status_code=404,
                content={"code": "not_found", "message": "资源不存在"},
            )
        target = (frontend_dist / spa_path).resolve()
        if frontend_dist.resolve() in target.parents and target.is_file():
            return FileResponse(target)
        index = frontend_dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            status_code=404,
            content={
                "code": "frontend_not_built",
                "message": "前端尚未构建，请使用 Vite 开发服务器或运行 npm run build",
            },
        )

    return app


app = create_app()
