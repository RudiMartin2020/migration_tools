#!/usr/bin/env bash
#
# flopi-db-sync 최초 1회 설치 스크립트 (리눅스)
#   - 사내 Nexus 에서 의존성 설치 (uv sync)
#   - .env 준비
#
# 인증 Nexus 라면 실행 전 자격증명을 export 하세요:
#   export UV_INDEX_NEXUS_USERNAME=계정 UV_INDEX_NEXUS_PASSWORD=비밀번호
#   (사내 인증서 TLS 오류 시) export UV_NATIVE_TLS=true
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "[ERROR] uv 가 없습니다. 폐쇄망이면 사내 배포본/오프라인 설치로 uv 를 먼저 설치하세요." >&2
  echo "        설치 후 다시 ./setup.sh 를 실행하세요." >&2
  exit 127
fi

echo "[1/2] 의존성 설치 (uv sync, Nexus) ..."
if ! uv sync; then
  echo "[ERROR] uv sync 실패. Nexus 접근/인증/TLS 환경변수를 확인하세요." >&2
  exit 1
fi

echo "[2/2] .env 확인 ..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "  -> .env 를 생성했습니다. 접속정보를 입력하세요: $SCRIPT_DIR/.env"
  echo "     (PG_PASSWORD, SQLITE_PATH 등)"
else
  echo "  -> .env 이미 존재"
fi

echo ""
echo "설치 완료. 실행 예시:"
echo "  ./sync.sh ds_tools -v"
echo "  ./sync.sh ds_tools --incremental updated_at"
