"""명령행 진입점.  사용법:  flopi-sync <테이블명> [<테이블명> ...]"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import load_settings
from .sync import sync_table


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="flopi-sync",
        description="SQLite -> PostgreSQL 테이블 동기화 (PK 기준 upsert)",
    )
    parser.add_argument("tables", nargs="+", help="동기화할 테이블명 (여러 개 가능)")
    parser.add_argument(
        "--delete", action="store_true",
        help="원본에 없는 행을 PG에서 삭제(미러 동기화)",
    )
    parser.add_argument(
        "--incremental", metavar="COL",
        help="해당 컬럼 워터마크 기준 증분 동기화 (변경분만 전송)",
    )
    parser.add_argument(
        "--full-refresh", action="store_true",
        help="저장된 증분 워터마크를 무시하고 전체 재동기화",
    )
    parser.add_argument(
        "--sync-sequence", action="store_true",
        help="동기화 후 PK 시퀀스(SERIAL/IDENTITY)를 최댓값으로 보정 (탐지는 항상 수행)",
    )
    parser.add_argument(
        "--insert-only", action="store_true",
        help="PK 중복은 무시하고 신규만 INSERT (변경분 UPDATE 안 함, ON CONFLICT DO NOTHING)",
    )
    parser.add_argument(
        "--add-columns", action="store_true",
        help="원본에 있고 PG에 없는 컬럼을 ALTER ADD COLUMN 으로 자동 추가",
    )
    parser.add_argument(
        "--migrate-dry-run", action="store_true",
        help="ADD COLUMN DDL 만 출력하고 실제 실행·동기화는 하지 않음 (--add-columns 와 함께)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="상세 로그")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("flopi_db_sync")

    try:
        settings = load_settings()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 2

    # 삭제 동기화는 .env 정책(ALLOW_DELETE)으로만 허용 — 실수 방지 가드
    if args.delete and not settings.allow_delete:
        log.error(
            "삭제 동기화(--delete)가 정책상 비활성화되어 있습니다. "
            "허용하려면 .env 에 ALLOW_DELETE=true 를 설정하세요."
        )
        return 2

    exit_code = 0
    for table in args.tables:
        try:
            result = sync_table(
                table,
                settings,
                incremental_col=args.incremental,
                delete=args.delete,
                full_refresh=args.full_refresh,
                sync_sequence=args.sync_sequence or settings.sync_sequence,
                insert_only=args.insert_only or settings.insert_only,
                add_columns=args.add_columns or settings.add_columns,
                migrate_dry_run=args.migrate_dry_run,
            )
            if result.dry_run:
                log.info("[dry-run] %s | 추가 예정 컬럼: %s",
                         result.table, result.added_columns or "없음")
                continue
            seq_note = ""
            if result.pk_sequences:
                seq_note = " | 시퀀스 " + ("보정됨" if result.sequence_synced else "감지(보정안함)")
            col_note = f" | 컬럼추가 {result.added_columns}" if result.added_columns else ""
            log.info(
                "완료: %s | 모드 %s | 전송 %d행 | 반영(insert+update) %d행 | 삭제 %d행%s%s",
                result.table,
                result.mode,
                result.read_rows,
                result.affected_rows,
                result.deleted_rows,
                seq_note,
                col_note,
            )
        except Exception as exc:  # noqa: BLE001 - 테이블 단위로 격리
            log.error("실패: %s | %s", table, exc)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
