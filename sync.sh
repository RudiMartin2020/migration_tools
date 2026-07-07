#!/usr/bin/env bash
#
# flopi-db-sync 실행 래퍼 (리눅스 운영용)
#   사용법: ./sync.sh <테이블명> [추가 테이블...] [옵션]
#   예)    ./sync.sh ds_tools
#          ./sync.sh ds_tools --incremental updated_at
#          ./sync.sh ds_tools tool_log --incremental updated_at -v
#          ALLOW_DELETE 는 .env 에서 제어 (--delete 사용 시)
#
# 어느 위치(cron 포함)에서 실행해도 동작하도록 스크립트 폴더로 이동한다.
# 종료코드: 0 정상 / 1 일부 테이블 실패 / 2 설정·인자 오류 / 127 uv 없음
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 로그 파일 (날짜별). LOG_DIR 환경변수로 위치 변경 가능.
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/flopi-sync-$(date +%Y%m%d).log"

# uv 설치 확인
if ! command -v uv >/dev/null 2>&1; then
  echo "[ERROR] uv 가 설치되어 있지 않습니다. (먼저 ./setup.sh 또는 uv 설치)" >&2
  exit 127
fi

# 인자 없으면 사용법 출력
if [ "$#" -eq 0 ]; then
  cat <<'USAGE'
사용법: ./sync.sh <테이블명> [추가 테이블...] [옵션]
  옵션:
    --incremental <컬럼>   증분 동기화 (변경분만)
    --full-refresh         증분 워터마크 무시하고 전체 재동기화
    --delete               원본에 없는 행 삭제 (.env ALLOW_DELETE=true 필요)
    --sync-sequence        동기화 후 PK 시퀀스 보정
    -v                     상세 로그
  예:
    ./sync.sh ds_tools
    ./sync.sh ds_tools --incremental updated_at
    ./sync.sh ds_tools tool_log --incremental updated_at -v
USAGE
  exit 2
fi

echo "===== $(date '+%F %T') START: flopi-sync $* =====" | tee -a "$LOG_FILE"
uv run flopi-sync "$@" 2>&1 | tee -a "$LOG_FILE"
rc=${PIPESTATUS[0]}
echo "===== $(date '+%F %T') END (rc=$rc) =====" | tee -a "$LOG_FILE"
exit "$rc"
