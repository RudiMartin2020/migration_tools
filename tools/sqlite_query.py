#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sqlite_query.py — SQLite 파일에 접속해 쿼리 결과를 보는 단독 실행 도구.

의존성 없음(표준 라이브러리만). DB 는 항상 읽기전용(mode=ro)으로 열어
원본을 절대 변경하지 않는다.

사용법:
    python3 sqlite_query.py <DB파일경로>                 # 대화형 모드
    python3 sqlite_query.py <DB파일경로> -c "SELECT ..."  # 단발 쿼리
    python3 sqlite_query.py <DB파일경로> -t              # 테이블 목록만

대화형 명령:
    SQL ...;          세미콜론(;)까지 입력하면 실행 (여러 줄 가능)
    .tables           테이블 목록
    .schema [테이블]   스키마(DDL) 출력
    .count 테이블      행 수
    .quit / .exit     종료
"""

import argparse
import os
import sqlite3
import sys

MAX_COL_WIDTH = 40     # 컬럼값 표시 최대 폭
DEFAULT_LIMIT = 200    # 대화형에서 한 번에 보여줄 최대 행 수


def connect_ro(path: str) -> sqlite3.Connection:
    if not os.path.isfile(path):
        sys.exit(f"오류: SQLite 파일이 없습니다: {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("SELECT 1")
        return conn
    except sqlite3.Error as exc:
        sys.exit(f"오류: SQLite 파일을 열 수 없습니다: {exc}")


def fmt(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return f"<BLOB {len(value)}B>"
    s = str(value)
    return s if len(s) <= MAX_COL_WIDTH else s[: MAX_COL_WIDTH - 1] + "…"


def print_rows(cur: sqlite3.Cursor, limit: int = 0) -> None:
    """커서 결과를 컬럼 폭 맞춰 표 형태로 출력."""
    if cur.description is None:          # SELECT 가 아닌 문장
        print(f"(완료 — {cur.rowcount if cur.rowcount >= 0 else 0}행 영향)")
        return

    headers = [d[0] for d in cur.description]
    rows = cur.fetchmany(limit) if limit else cur.fetchall()
    truncated = bool(limit) and bool(cur.fetchone())

    table = [[fmt(v) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in table:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(v))

    sep = "-+-".join("-" * w for w in widths)
    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print(sep)
    for row in table:
        print(" | ".join(v.ljust(w) for v, w in zip(row, widths)))
    print(f"({len(table)}행" + (f", 이후 생략 — LIMIT 을 지정하세요" if truncated else "") + ")")


def run_sql(conn: sqlite3.Connection, sql: str, limit: int = 0) -> None:
    try:
        cur = conn.execute(sql)
        print_rows(cur, limit)
    except sqlite3.Error as exc:
        print(f"SQL 오류: {exc}")


def cmd_tables(conn: sqlite3.Connection) -> None:
    run_sql(conn, "SELECT name AS table_name FROM sqlite_master "
                  "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")


def cmd_schema(conn: sqlite3.Connection, table: str = "") -> None:
    if table:
        cur = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = ? AND sql IS NOT NULL",
            (table,))
    else:
        cur = conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")
    rows = cur.fetchall()
    if not rows:
        print(f"스키마 없음: {table or '(전체)'}")
    for (ddl,) in rows:
        print(ddl.rstrip() + ";")


def interactive(conn: sqlite3.Connection, path: str) -> None:
    print(f"SQLite 읽기전용 접속: {path}")
    print("SQL 은 ';' 로 끝내면 실행. 명령: .tables .schema [테이블] "
          f".count 테이블 .quit  (SELECT 는 최대 {DEFAULT_LIMIT}행 표시)")
    buf: list[str] = []
    while True:
        try:
            prompt = "sqlite> " if not buf else "   ...> "
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            break

        line = line.lstrip("﻿")   # 파이프 입력 BOM 방어
        stripped = line.strip()
        if not buf and stripped.startswith("."):
            parts = stripped.split()
            cmd, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
            if cmd in (".quit", ".exit"):
                break
            elif cmd == ".tables":
                cmd_tables(conn)
            elif cmd == ".schema":
                cmd_schema(conn, arg)
            elif cmd == ".count" and arg:
                run_sql(conn, f'SELECT COUNT(*) AS rows FROM "{arg}"')
            else:
                print("명령: .tables | .schema [테이블] | .count 테이블 | .quit")
            continue

        if not stripped and not buf:
            continue
        buf.append(line)
        if stripped.endswith(";"):
            sql = "\n".join(buf).strip().rstrip(";")
            buf.clear()
            if sql:
                run_sql(conn, sql, DEFAULT_LIMIT)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="SQLite 파일 쿼리 도구 (읽기전용)")
    ap.add_argument("db", help="SQLite DB 파일 경로")
    ap.add_argument("-c", "--command", metavar="SQL",
                    help="단발 쿼리 실행 후 종료 (전체 행 출력)")
    ap.add_argument("-t", "--tables", action="store_true",
                    help="테이블 목록만 출력 후 종료")
    args = ap.parse_args()

    conn = connect_ro(args.db)
    try:
        if args.tables:
            cmd_tables(conn)
        elif args.command:
            run_sql(conn, args.command)      # 단발은 제한 없이 전체 출력
        else:
            interactive(conn, args.db)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
