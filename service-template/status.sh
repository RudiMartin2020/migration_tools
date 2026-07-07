#!/usr/bin/env bash
# FastAPI 서비스 상태.  사용법: ./status.sh   (실행중 rc=0 / 중지 rc=1)
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ===== 프로젝트별 설정 (start.sh 의 APP_NAME 과 동일하게) =====
APP_NAME="fastapi-app"
# =============================================================

PID_FILE="run/${APP_NAME}.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "$APP_NAME 실행중 (PID $(cat "$PID_FILE"))"; exit 0
else
  echo "$APP_NAME 중지됨"; exit 1
fi
