#!/usr/bin/env bash
#
# cron-sync.sh 를 현재 사용자 crontab 에 등록한다 (중복 등록 방지).
#   사용:  ./cron-install.sh           # 등록/갱신
#          ./cron-install.sh remove     # 등록 해제
#
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB="$DIR/cron-sync.sh"

# ===== 실행 주기 (cron 표현식, 필요시 수정) =====
SCHEDULE="0 * * * *"           # 매시 정각 (1시간마다)
# ===============================================

mkdir -p "$DIR/logs"
CRON_LINE="$SCHEDULE $JOB >> $DIR/logs/cron.log 2>&1"

if [ "${1:-}" = "remove" ]; then
  crontab -l 2>/dev/null | grep -v -F "$JOB" | crontab -
  echo "등록 해제됨: $JOB"
  exit 0
fi

if [ ! -x "$JOB" ]; then
  chmod +x "$JOB" "$DIR/sync.sh" 2>/dev/null || true
fi

# 기존 동일 작업 제거 후 새로 추가 (idempotent)
( crontab -l 2>/dev/null | grep -v -F "$JOB"; echo "$CRON_LINE" ) | crontab -

echo "등록 완료:"
crontab -l | grep -F "$JOB"
