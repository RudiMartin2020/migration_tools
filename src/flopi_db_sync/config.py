"""환경설정 로딩 (.env 또는 OS 환경변수)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    sqlite_path: str
    pg_host: str
    pg_port: int
    pg_user: str
    pg_password: str
    pg_db: str
    pg_schema: str
    batch_size: int
    state_file: str
    allow_delete: bool
    sync_sequence: bool
    insert_only: bool
    add_columns: bool

    @property
    def pg_conninfo(self) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} "
            f"dbname={self.pg_db} user={self.pg_user} "
            f"password={self.pg_password}"
        )


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"환경변수 {name} 가 설정되지 않았습니다 (.env 확인).")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def load_settings() -> Settings:
    return Settings(
        sqlite_path=_require("SQLITE_PATH"),
        pg_host=_require("PG_HOST"),
        pg_port=int(os.getenv("PG_PORT", "5432")),
        pg_user=_require("PG_USER"),
        pg_password=_require("PG_PASSWORD"),
        pg_db=_require("PG_DB"),
        pg_schema=os.getenv("PG_SCHEMA", "public"),
        batch_size=int(os.getenv("BATCH_SIZE", "1000")),
        state_file=os.getenv("STATE_FILE", ".flopi_db_sync_state.json"),
        allow_delete=_env_bool("ALLOW_DELETE", default=False),
        sync_sequence=_env_bool("SYNC_SEQUENCE", default=False),
        insert_only=_env_bool("INSERT_ONLY", default=False),
        add_columns=_env_bool("ADD_COLUMNS", default=False),
    )
