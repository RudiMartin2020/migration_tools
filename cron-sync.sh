#!/usr/bin/env bash
#
# cron 정기 동기화 작업 — ds_tools, chat_scenarios 증분 동기화
#   crontab 에서 이 스크립트를 호출한다 (cron-install.sh 로 등록).
#   sync.sh 와 같은 폴더에 있어야 한다.
#
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# cron 은 최소 PATH 만 가지므로 uv 경로를 보강한다.
# (which uv 결과에 맞게 수정 — 보통 ~/.local/bin 또는 /usr/local/bin)
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# (인증/TLS 가 필요한 사내 Nexus면 주석 해제)
# export UV_INDEX_NEXUS_USERNAME="..." UV_INDEX_NEXUS_PASSWORD="..."
# export UV_NATIVE_TLS=true

# ===== 동기화 대상/옵션 (필요시 수정) =====
TABLES="ds_tools chat_scenarios"
INCREMENTAL_COL="updated_at"
# =========================================

# --insert-only: 중복(PK·보조 UNIQUE)인 행은 스킵하고 신규만 INSERT
#   (기존 행의 값 변경은 반영되지 않음 — upsert 로 되돌리려면 플래그 제거)
./sync.sh $TABLES --incremental "$INCREMENTAL_COL" --insert-only -v
