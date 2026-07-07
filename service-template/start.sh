#!/usr/bin/env bash
# FastAPI 서비스 시작.  사용법: ./start.sh
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ===== 프로젝트별 설정 (여기만 수정) ==========================
APP_NAME="fastapi-app"
CMD="uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"
# =============================================================

PID_FILE="run/${APP_NAME}.pid"
LOG_FILE="log/${APP_NAME}.log"
mkdir -p run log

# 이미 실행중이면 중복 기동 방지
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "$APP_NAME 이미 실행중 (PID $(cat "$PID_FILE"))"; exit 0
fi

# 새 세션(프로세스 그룹)으로 띄워 stop 시 자식까지 함께 종료되게 한다
setsid bash -c "exec $CMD" >>"$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
sleep 1

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "$APP_NAME 시작됨 (PID $(cat "$PID_FILE"))  로그: $LOG_FILE"
else
  echo "$APP_NAME 시작 실패 — 로그 확인: $LOG_FILE"; rm -f "$PID_FILE"; exit 1
fi
