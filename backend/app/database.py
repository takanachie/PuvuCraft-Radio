from __future__ import annotations

from collections.abc import Iterator

from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base, Channel, MusicLibrary, PlaybackState, User


class Database:
    def __init__(self, settings: Settings) -> None:
        connect_args: dict[str, object] = {}
        if settings.database.url.startswith("sqlite:"):
            connect_args["check_same_thread"] = False
        self.settings = settings
        self.engine = create_engine(
            settings.database.url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        if make_url(settings.database.url).get_backend_name() == "sqlite":
            self._configure_sqlite(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    def _configure_sqlite(self, engine: Engine) -> None:
        settings = self.settings

        @event.listens_for(engine, "connect")
        def set_pragmas(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={settings.database.busy_timeout_ms}")
            if settings.database.sqlite_wal:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    def initialize(self) -> None:
        if self.settings.app.environment == "production":
            self._verify_migration_revision()
        else:
            Base.metadata.create_all(self.engine)
        self._seed_music_libraries()
        self._seed_channels()

    def _verify_migration_revision(self) -> None:
        ini_path = self.settings.config_path.parent / "alembic.ini"
        if not ini_path.is_file():
            raise RuntimeError(f"Alembic configuration not found: {ini_path}")
        script = ScriptDirectory.from_config(AlembicConfig(str(ini_path)))
        expected = set(script.get_heads())
        with self.engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
        if current != expected:
            raise RuntimeError(
                "database migration is not current; run `alembic upgrade head` "
                f"(database={sorted(current)}, expected={sorted(expected)})"
            )

    def _seed_music_libraries(self) -> None:
        with self.session_factory.begin() as session:
            if session.get(MusicLibrary, "default") is None:
                session.add(MusicLibrary(name="default"))

    def _seed_channels(self) -> None:
        with self.session_factory.begin() as session:
            count = session.scalar(select(func.count(Channel.id))) or 0
            if count:
                return
            for item in self.settings.seed.channels:
                channel = Channel(
                    name=item.name,
                    slug=item.slug,
                    description=item.description,
                    enabled=item.enabled,
                    playback_mode=item.playback_mode,
                    display_order=item.display_order,
                )
                channel.playback_state = PlaybackState(
                    status="starting" if item.enabled else "stopped"
                )
                session.add(channel)

    def has_admin(self) -> bool:
        with self.session_factory() as session:
            return bool(session.scalar(select(func.count(User.id)).where(User.role == "admin")))

    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    def close(self) -> None:
        self.engine.dispose()
