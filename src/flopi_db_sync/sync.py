"""SQLite -> PostgreSQL 테이블 동기화 핵심 로직.

지원 기능
  - PK 기준 upsert: 없는 행 INSERT / 변경된 행만 UPDATE
  - 타입 변환 계층(#1): PG information_schema 타입 기준 안전 변환(boolean/bytea 등)
  - 삭제 동기화(#7): --delete 시 원본에 없는 행을 PG에서 제거(미러)
  - 증분 동기화(#6): --incremental COL 워터마크 기준 변경분만 전송
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Optional

import psycopg
from psycopg import sql

from .config import Settings
from . import state as state_mod

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 식별자 / 메타데이터
# --------------------------------------------------------------------------- #
def _q(ident: str) -> str:
    """SQLite/PG 공용 식별자 더블쿼팅."""
    return '"' + ident.replace('"', '""') + '"'


def _validate_identifier(name: str) -> None:
    if not name or not all(c.isalnum() or c == "_" for c in name):
        raise ValueError(f"허용되지 않는 식별자: {name!r} (영숫자/언더스코어만)")


@dataclass
class TableMeta:
    columns: list[str]
    pk: list[str]
    types: dict[str, str]  # 컬럼 → SQLite 선언 타입 문자열


@dataclass
class SyncResult:
    table: str
    mode: str
    read_rows: int       # 원본에서 읽어 전송한 행 수
    affected_rows: int   # INSERT + 실제 UPDATE 된 행 수
    deleted_rows: int    # 삭제 동기화로 제거된 행 수
    pk_sequences: dict = field(default_factory=dict)  # 탐지된 PK 시퀀스 {컬럼: 시퀀스명}
    sequence_synced: bool = False                     # 시퀀스 보정 수행 여부
    added_columns: dict = field(default_factory=dict)  # ALTER 로 추가된 {컬럼: PG타입}
    dry_run: bool = False                              # 마이그레이션 dry-run 여부


def _read_meta(scur: sqlite3.Cursor, table: str) -> TableMeta:
    scur.execute(f"PRAGMA table_info({_q(table)})")
    info = scur.fetchall()  # (cid, name, type, notnull, dflt, pk)
    if not info:
        raise ValueError(f"SQLite에 테이블이 존재하지 않습니다: {table}")
    columns = [row[1] for row in info]
    types = {row[1]: (row[2] or "") for row in info}
    pk_rows = sorted((row for row in info if row[5] > 0), key=lambda r: r[5])
    pk = [row[1] for row in pk_rows]
    if not pk:
        raise ValueError(f"PRIMARY KEY가 없어 동기화할 수 없습니다: {table}")
    return TableMeta(columns=columns, pk=pk, types=types)


# --------------------------------------------------------------------------- #
# 타입 변환 계층 (#1)
# --------------------------------------------------------------------------- #
_BOOL_TRUE = {"1", "t", "true", "y", "yes", "on"}


def _to_bool(v):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in _BOOL_TRUE
    return bool(v)


def _to_bytea(v):
    if v is None or isinstance(v, (bytes, bytearray, memoryview)):
        return None if v is None else bytes(v)
    if isinstance(v, str):
        return v.encode("utf-8")
    return v


def _converter_for(pg_type: str) -> Optional[Callable]:
    t = pg_type.lower()
    if t == "boolean":
        return _to_bool
    if t == "bytea":
        return _to_bytea
    return None  # 그 외는 psycopg/PG 기본 어댑팅에 위임 (identity)


def _pg_column_types(pcur, schema: str, table: str) -> dict[str, str]:
    pcur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s",
        (schema, table),
    )
    types = {row[0]: row[1] for row in pcur.fetchall()}
    if not types:
        raise ValueError(
            f"PostgreSQL에 대상 테이블이 없습니다: {schema}.{table} "
            "(대상 테이블/스키마를 먼저 생성하세요)"
        )
    return types


def _build_converters(meta: TableMeta, pg_types: dict[str, str]) -> list[Optional[Callable]]:
    missing = [c for c in meta.columns if c not in pg_types]
    if missing:
        raise ValueError(
            f"PG 대상 테이블에 없는 컬럼: {missing} (스키마 불일치)"
        )
    return [_converter_for(pg_types[c]) for c in meta.columns]


def _convert_row(row, converters: list[Optional[Callable]]):
    return tuple(conv(val) if conv else val for conv, val in zip(converters, row))


# --------------------------------------------------------------------------- #
# 스키마 자동 반영 (ALTER ADD COLUMN) — SQLite 신규 컬럼을 PG에 추가
# --------------------------------------------------------------------------- #
def _sqlite_type_to_pg(decl: str) -> str:
    """SQLite 선언 타입 문자열 → PostgreSQL 타입 (2단계: 키워드 정밀 → affinity 폴백)."""
    t = (decl or "").upper()
    if "BOOL" in t:
        return "boolean"
    if "DATETIME" in t or "TIMESTAMP" in t:
        return "timestamp"
    if "DATE" in t:
        return "date"
    if "DECIMAL" in t or "NUMERIC" in t:
        m = re.search(r"\(\s*(\d+)\s*,\s*(\d+)\s*\)", t)
        return f"numeric({m.group(1)},{m.group(2)})" if m else "numeric"
    if any(k in t for k in ("CHAR", "CLOB", "TEXT")):
        return "text"
    if "INT" in t:
        return "bigint"
    if any(k in t for k in ("REAL", "FLOA", "DOUB")):
        return "double precision"
    if "BLOB" in t:
        return "bytea"
    if not t:
        return "text"  # 미선언 컬럼은 안전하게 text
    return "numeric"


def _add_missing_columns(
    pcur, schema: str, table: str, missing: list[str],
    sqlite_types: dict[str, str], dry_run: bool,
) -> dict[str, str]:
    """누락 컬럼을 PG에 ALTER ADD COLUMN (nullable, IF NOT EXISTS). dry_run 시 DDL만 로깅."""
    added: dict[str, str] = {}
    for col in missing:
        pg_type = _sqlite_type_to_pg(sqlite_types.get(col, ""))
        stmt = sql.SQL(
            "ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} " + pg_type
        ).format(tbl=sql.Identifier(schema, table), col=sql.Identifier(col))
        ddl = stmt.as_string(pcur)
        if dry_run:
            logger.info("[dry-run] %s", ddl)
        else:
            logger.info("컬럼 추가: %s", ddl)
            pcur.execute(stmt)
        added[col] = pg_type
    return added


def _warn_schema_diff(pcur, schema: str, table: str, meta: TableMeta,
                      pg_types: dict[str, str]) -> None:
    """파괴적/모호 변경은 자동 반영하지 않고 경고만 남긴다."""
    # PG에는 있는데 원본에 없는 컬럼 (삭제 후보) — 삭제하지 않음
    extra = [c for c in pg_types if c not in meta.columns]
    if extra:
        logger.warning("PG에만 있는 컬럼(원본에 없음, 삭제 안 함): %s", extra)


# --------------------------------------------------------------------------- #
# Upsert 문 생성
# --------------------------------------------------------------------------- #
def _build_upsert(
    schema: str, table: str, meta: TableMeta, insert_only: bool = False
) -> sql.Composed:
    table_ident = sql.Identifier(schema, table)
    col_idents = [sql.Identifier(c) for c in meta.columns]
    non_pk = [c for c in meta.columns if c not in meta.pk]

    cols = sql.SQL(", ").join(col_idents)
    placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in meta.columns)
    conflict = sql.SQL(", ").join(sql.Identifier(c) for c in meta.pk)

    # insert_only: PK 중복은 무시(갱신 안 함) / non_pk 없을 때도 갱신할 게 없음
    if insert_only or not non_pk:
        return sql.SQL(
            "INSERT INTO {tbl} ({cols}) VALUES ({vals}) "
            "ON CONFLICT ({conflict}) DO NOTHING"
        ).format(tbl=table_ident, cols=cols, vals=placeholders, conflict=conflict)

    updates = sql.SQL(", ").join(
        sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in non_pk
    )
    distinct = sql.SQL(" OR ").join(
        sql.SQL("{t}.{c} IS DISTINCT FROM EXCLUDED.{c}").format(
            t=sql.Identifier(table), c=sql.Identifier(c)
        )
        for c in non_pk
    )
    return sql.SQL(
        "INSERT INTO {tbl} ({cols}) VALUES ({vals}) "
        "ON CONFLICT ({conflict}) DO UPDATE SET {updates} WHERE {distinct}"
    ).format(
        tbl=table_ident, cols=cols, vals=placeholders,
        conflict=conflict, updates=updates, distinct=distinct,
    )


# --------------------------------------------------------------------------- #
# 삭제 동기화 (#7)
# --------------------------------------------------------------------------- #
def _sync_deletes(
    sconn: sqlite3.Connection, pcur, settings: Settings,
    table: str, meta: TableMeta, pg_types: dict[str, str],
) -> int:
    """원본 PK 전체를 temp 테이블로 적재 후 anti-join 으로 PG 잉여행 삭제."""
    pk_defs = ", ".join(f"{_q(c)} {pg_types[c]}" for c in meta.pk)
    pk_cols = ", ".join(_q(c) for c in meta.pk)
    pcur.execute(f"CREATE TEMP TABLE _flopi_src_pk ({pk_defs}) ON COMMIT DROP")

    pk_conv = [_converter_for(pg_types[c]) for c in meta.pk]
    scur = sconn.cursor()
    scur.execute(f"SELECT {pk_cols} FROM {_q(table)}")
    copy_sql = f"COPY _flopi_src_pk ({pk_cols}) FROM STDIN"
    with pcur.copy(copy_sql) as cp:
        while True:
            batch = scur.fetchmany(settings.batch_size)
            if not batch:
                break
            for r in batch:
                cp.write_row(_convert_row(r, pk_conv))

    cond = " AND ".join(f"s.{_q(c)} = t.{_q(c)}" for c in meta.pk)
    pcur.execute(
        f"DELETE FROM {_q(settings.pg_schema)}.{_q(table)} t "
        f"WHERE NOT EXISTS (SELECT 1 FROM _flopi_src_pk s WHERE {cond})"
    )
    return pcur.rowcount or 0


# --------------------------------------------------------------------------- #
# 시퀀스(SERIAL/IDENTITY) 탐지 & 보정
# --------------------------------------------------------------------------- #
def _pk_sequences(pcur, schema: str, table: str, pk: list[str]) -> dict[str, str]:
    """PK 컬럼에 연결된 시퀀스명을 반환 (SERIAL/IDENTITY 자동 탐지, 읽기전용).

    pg_get_serial_sequence 가 NULL 이면 시퀀스 없는 컬럼이므로 결과에서 제외한다.
    """
    seqs: dict[str, str] = {}
    for col in pk:
        pcur.execute(
            "SELECT pg_get_serial_sequence(%s, %s)", (f"{schema}.{table}", col)
        )
        seq = pcur.fetchone()[0]
        if seq:
            seqs[col] = seq
    return seqs


def _sync_sequences(pcur, schema: str, table: str, seqs: dict[str, str]) -> dict[str, int]:
    """각 시퀀스를 해당 컬럼 현재 최댓값으로 보정 → 다음 nextval 충돌 방지."""
    result: dict[str, int] = {}
    for col, seq in seqs.items():
        pcur.execute(
            sql.SQL(
                "SELECT setval(%s, (SELECT COALESCE(MAX({col}), 1) FROM {tbl}))"
            ).format(col=sql.Identifier(col), tbl=sql.Identifier(schema, table)),
            (seq,),
        )
        result[col] = pcur.fetchone()[0]
    return result


# --------------------------------------------------------------------------- #
# 메인 동기화
# --------------------------------------------------------------------------- #
def sync_table(
    table: str,
    settings: Settings,
    *,
    incremental_col: Optional[str] = None,
    delete: bool = False,
    full_refresh: bool = False,
    sync_sequence: bool = False,
    insert_only: bool = False,
    add_columns: bool = False,
    migrate_dry_run: bool = False,
) -> SyncResult:
    _validate_identifier(table)
    if incremental_col:
        _validate_identifier(incremental_col)
    if delete and not settings.allow_delete:
        raise PermissionError(
            "삭제 동기화가 비활성화되어 있습니다 (.env ALLOW_DELETE=true 필요)"
        )
    # SQLITE_PATH 는 실제 DB '파일'이어야 한다 (없으면 빈 DB 자동생성 방지)
    if not os.path.isfile(settings.sqlite_path):
        raise FileNotFoundError(
            f"SQLite DB 파일을 찾을 수 없습니다: {settings.sqlite_path} "
            "(SQLITE_PATH 에 디렉터리가 아닌 파일명까지 포함한 경로를 지정하세요)"
        )

    read_rows = 0
    affected = 0
    deleted = 0
    pk_seqs: dict[str, str] = {}
    seq_synced = False
    added_cols: dict[str, str] = {}
    mode = "incremental" if incremental_col else "full"

    # 원본 SQLite 는 읽기전용으로 연다 (OS/SQLite 레벨에서 쓰기 차단)
    sconn = sqlite3.connect(f"file:{settings.sqlite_path}?mode=ro", uri=True)
    try:
        scur = sconn.cursor()
        meta = _read_meta(scur, table)
        if incremental_col and incremental_col not in meta.columns:
            raise ValueError(f"증분 기준 컬럼이 테이블에 없습니다: {incremental_col}")
        logger.info("테이블 %s | 컬럼 %d | PK %s | 모드 %s%s",
                    table, len(meta.columns), meta.pk, mode,
                    " +delete" if delete else "")

        # 증분 워터마크 로딩
        st = {}
        last_wm = None
        if incremental_col:
            st = state_mod.load_state(settings.state_file)
            if not full_refresh:
                last_wm = state_mod.get_watermark(st, table, incremental_col)
            logger.info("증분 기준=%s, 직전 워터마크=%r%s",
                        incremental_col, last_wm,
                        " (full-refresh)" if full_refresh else "")

        with psycopg.connect(settings.pg_conninfo, application_name="flopi-sync") as pconn:
            with pconn.cursor() as pcur:
                pg_types = _pg_column_types(pcur, settings.pg_schema, table)

                # 스키마 자동 반영: 원본에 있고 PG에 없는 컬럼 처리
                missing = [c for c in meta.columns if c not in pg_types]
                if missing and add_columns:
                    added_cols = _add_missing_columns(
                        pcur, settings.pg_schema, table, missing, meta.types,
                        migrate_dry_run,
                    )
                    if not migrate_dry_run:
                        pg_types = _pg_column_types(pcur, settings.pg_schema, table)  # 재조회

                # dry-run 은 어떤 경우에도 데이터 동기화를 수행하지 않는다
                # (누락 컬럼이 없거나 add_columns 미지정이어도 안전하게 종료)
                if migrate_dry_run:
                    pconn.rollback()
                    if not (missing and add_columns):
                        logger.info("[dry-run] 추가할 컬럼 없음")
                    logger.info("[dry-run] 데이터 동기화는 수행하지 않음")
                    return SyncResult(
                        table=table, mode=mode, read_rows=0, affected_rows=0,
                        deleted_rows=0, added_columns=added_cols, dry_run=True,
                    )

                _warn_schema_diff(pcur, settings.pg_schema, table, meta, pg_types)

                converters = _build_converters(meta, pg_types)
                upsert = _build_upsert(settings.pg_schema, table, meta, insert_only)
                if insert_only:
                    logger.info("insert-only 모드: PK 중복은 무시(갱신 안 함)")

                # PK 시퀀스(SERIAL/IDENTITY) 탐지 — 읽기전용, 항상 수행
                pk_seqs = _pk_sequences(pcur, settings.pg_schema, table, meta.pk)
                if pk_seqs:
                    logger.info("PK 시퀀스 감지: %s", pk_seqs)
                else:
                    logger.info("PK 시퀀스 없음 (시퀀스 보정 불필요)")

                # SQLite SELECT (증분 필터 + 정렬)
                # 경계값을 '>=' 로 재처리: 같은 시각(초 단위) 행 누락 방지.
                # upsert 가 멱등이므로 경계 행 재전송은 무해하다.
                col_list = ", ".join(_q(c) for c in meta.columns)
                select = f"SELECT {col_list} FROM {_q(table)}"
                params: list = []
                if incremental_col and last_wm is not None:
                    select += f" WHERE {_q(incremental_col)} >= ?"
                    params.append(last_wm)
                if incremental_col:
                    select += f" ORDER BY {_q(incremental_col)}"
                scur.execute(select, params)

                inc_idx = meta.columns.index(incremental_col) if incremental_col else None
                max_wm = last_wm

                while True:
                    rows = scur.fetchmany(settings.batch_size)
                    if not rows:
                        break
                    if inc_idx is not None:
                        for r in rows:
                            v = r[inc_idx]
                            if v is not None and (max_wm is None or v > max_wm):
                                max_wm = v
                    converted = [_convert_row(r, converters) for r in rows]
                    pcur.executemany(upsert, converted)
                    read_rows += len(rows)
                    if pcur.rowcount and pcur.rowcount > 0:
                        affected += pcur.rowcount
                    logger.info("진행: %d행 전송", read_rows)

                # 삭제 동기화 (같은 트랜잭션 내, upsert 이후)
                if delete:
                    deleted = _sync_deletes(sconn, pcur, settings, table, meta, pg_types)
                    logger.info("삭제 동기화: %d행 제거", deleted)

            pconn.commit()

            # 커밋 후 시퀀스 보정 (옵션) — 시퀀스가 있을 때만
            if sync_sequence and pk_seqs:
                with pconn.cursor() as scur2:
                    res = _sync_sequences(scur2, settings.pg_schema, table, pk_seqs)
                pconn.commit()
                seq_synced = True
                logger.info("시퀀스 보정 완료: %s", res)
            elif sync_sequence and not pk_seqs:
                logger.info("시퀀스 보정 건너뜀 (PK에 연결된 시퀀스 없음)")

        # 커밋 성공 후 워터마크 저장
        if incremental_col and max_wm is not None:
            state_mod.set_watermark(st, table, incremental_col, max_wm)
            state_mod.save_state(settings.state_file, st)
            logger.info("워터마크 저장: %s=%r", incremental_col, max_wm)
    finally:
        sconn.close()

    return SyncResult(
        table=table, mode=mode, read_rows=read_rows,
        affected_rows=affected, deleted_rows=deleted,
        pk_sequences=pk_seqs, sequence_synced=seq_synced,
        added_columns=added_cols,
    )
