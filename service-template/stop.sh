#!/usr/bin/env bash
# FastAPI 서비스 중지.  사용법: ./stop.sh
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ===== 프로젝트별 설정 (start.sh 의 APP_NAME 과 동일하게) =====
APP_NAME="fastapi-app"
# =============================================================

PID_FILE="run/${APP_NAME}.pid"

if [ ! -f "$PID_FILE" ] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "$APP_NAME 실행중 아님"; rm -f "$PID_FILE"; exit 0
fi

pid="$(cat "$PID_FILE")"
kill -TERM -"$pid" 2>/dev/null             # 프로세스 그룹 전체에 종료 신호
for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
kill -0 "$pid" 2>/dev/null && kill -KILL -"$pid" 2>/dev/null  # 안 죽으면 강제 종료
rm -f "$PID_FILE"
echo "$APP_NAME 중지됨"
