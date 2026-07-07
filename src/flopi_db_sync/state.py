"""증분 동기화용 워터마크 상태 저장/로딩 (JSON 파일).

구조:
{
  "<table>": {"column": "<watermark_col>", "value": "<last_value>"}
}
"""

from __future__ import annotations

import json
import os
from typing import Any


def load_state(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path: str, state: dict[str, Any]) -> None:
    # 원자적 쓰기: 임시파일에 기록 후 교체
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(state, fp, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


def get_watermark(state: dict[str, Any], table: str, column: str):
    entry = state.get(table)
    if entry and entry.get("column") == column:
        return entry.get("value")
    return None


def set_watermark(state: dict[str, Any], table: str, column: str, value) -> None:
    state[table] = {"column": column, "value": value}
