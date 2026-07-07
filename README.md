# flopi-sync

SQLite 원본 DB를 PostgreSQL로 **동기화**하는 리눅스 단독 실행 도구.
테이블명을 인자로 받아, **PRIMARY KEY 기준**으로 없는 행은 `INSERT`,
컬럼값이 바뀐 행은 `UPDATE` 한다. (FastAPI 없이 CLI 단독 동작)

## 요구 환경
- Linux (CentOS Stream 9 컨테이너에서 검증 / 타깃 Stream 10)
- Python >= 3.9
- [uv](https://docs.astral.sh/uv/)
- 사내 Nexus PyPI 인덱스 접근 (아래 참고)

## 패키지 저장소 (사내 Nexus)

의존성은 인터넷 PyPI가 아닌 **사내 Nexus**에서 받도록 `pyproject.toml` 에
이미 설정되어 있다. (`default = true` 이므로 PyPI를 대체한다.)

```toml
[[tool.uv.index]]
name = "nexus"
url = "https://scpnexus.itplatform.samsundisplay.net:8081/nexus/repository/pypi/simple"
default = true
```

별도 설정 없이 `uv sync` 하면 위 인덱스에서 패키지를 내려받는다.

> ⚠️ `default = true` 이므로 **인터넷 PyPI로 폴백하지 않는다.** 운영 서버가
> 사내망(Nexus 접근 가능)이어야 설치된다. 사내망 밖에서 테스트할 때만
> `UV_DEFAULT_INDEX=https://pypi.org/simple uv sync` 로 임시 우회.

**인증이 필요한 Nexus라면** 자격증명을 환경변수로 전달한다 (URL에 비밀번호 하드코딩 금지):
```bash
export UV_INDEX_NEXUS_USERNAME="사번/계정"
export UV_INDEX_NEXUS_PASSWORD="비밀번호"
```

**사내 인증서(사설 CA)로 TLS 오류가 나는 경우**:
```bash
export UV_NATIVE_TLS=true                 # OS 신뢰 저장소 사용
# 또는 사내 CA 번들 지정
export SSL_CERT_FILE=/etc/pki/tls/certs/company-ca.pem
```

## 빠른 사용 (쉘 스크립트) — 리눅스 권장

복잡한 `uv run ...` 대신 래퍼 스크립트로 간단히 실행한다. (로그 자동 적재)

```bash
# 실행 권한 부여 (최초 1회)
chmod +x setup.sh sync.sh

# 최초 설치: 의존성(uv sync) + .env 준비
./setup.sh
vi .env                       # 접속정보 입력

# 동기화 실행 — 인자는 테이블명
./sync.sh ds_tools                                  # 전체
./sync.sh ds_tools --incremental updated_at         # 증분
./sync.sh ds_tools tool_log --incremental updated_at -v   # 여러 테이블
```

- 어느 위치에서 실행해도 스크립트 폴더 기준으로 동작 (cron 안전)
- 로그: `logs/flopi-sync-YYYYMMDD.log` 에 자동 기록
- 종료코드: `0` 정상 / `1` 일부 실패 / `2` 설정·인자 오류 / `127` uv 없음

> Windows에서 받은 스크립트라 줄바꿈 오류(`\r`)가 나면: `sed -i 's/\r$//' *.sh`

### cron 예시 (매일 02:00 증분)
```cron
0 2 * * *  /opt/flopi-sync/sync.sh ds_tools --incremental updated_at
```

## 설치 / 실행 (uv 직접)

```bash
# 1) uv 설치 (최초 1회, 폐쇄망이면 사내 배포본/오프라인 설치 사용)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2) 의존성 동기화 (.venv 자동 생성, Nexus에서 다운로드)
uv sync

# 3) 접속정보 설정
cp .env.example .env   # 값 확인/수정

# 4) 실행 — 인자는 테이블명
uv run flopi-sync <테이블명>
uv run flopi-sync users orders products   # 여러 테이블
uv run python -m flopi_db_sync users -v    # 상세 로그
```

### 옵션
| 옵션 | 설명 |
|------|------|
| `--delete` | 원본(SQLite)에 없는 행을 PG에서 삭제 (단방향 미러 동기화). **`.env` 의 `ALLOW_DELETE=true` 일 때만 동작** |
| `--incremental COL` | `COL` 워터마크 기준 변경분만 전송 (전체 스캔 회피) |
| `--full-refresh` | 저장된 워터마크 무시하고 전체 재동기화 |
| `--sync-sequence` | 동기화 후 PK 시퀀스(SERIAL/IDENTITY)를 최댓값으로 보정. **PK 시퀀스 탐지는 항상 수행**되고, 보정만 이 옵션(또는 `.env` `SYNC_SEQUENCE=true`)으로 켜짐 |
| `--insert-only` | PK 중복은 무시하고 신규만 INSERT (변경분 UPDATE 안 함, `ON CONFLICT DO NOTHING`). `.env` `INSERT_ONLY=true` 로도 가능 |
| `--add-columns` | 원본에 있고 PG에 없는 컬럼을 `ALTER ADD COLUMN` 으로 자동 추가 (테이블 생성 X, 삭제·타입변경은 경고만). `.env` `ADD_COLUMNS=true` 로도 가능 |
| `--migrate-dry-run` | `ADD COLUMN` DDL 만 출력하고 실행·동기화는 안 함 (`--add-columns` 와 함께) |
| `-v, --verbose` | 상세 로그 |

```bash
# 전체 upsert + 삭제 미러
uv run flopi-sync users --delete

# updated_at 기준 증분 (이후 실행은 변경분만)
uv run flopi-sync users --incremental updated_at

# 증분 + 삭제 동시 (삭제 감지는 PK 전체 비교로 수행)
uv run flopi-sync users --incremental updated_at --delete

# 워터마크 리셋 후 전체 재동기화
uv run flopi-sync users --incremental updated_at --full-refresh
```

설치형으로 쓰려면:
```bash
uv pip install -e .
flopi-sync <테이블명>
```

## 설정 (.env)
| 변수 | 설명 |
|------|------|
| `SQLITE_PATH` | SQLite 원본 DB **파일** 경로 (디렉터리 아님, 파일명까지 포함). 파일이 없으면 에러로 중단 |
| `PG_HOST` / `PG_PORT` | PostgreSQL 호스트 / 포트 |
| `PG_USER` / `PG_PASSWORD` | 접속 계정 |
| `PG_DB` | 데이터베이스명 |
| `PG_SCHEMA` | 대상 스키마 (기본 public) |
| `BATCH_SIZE` | 배치 처리 행 수 (기본 1000) |
| `STATE_FILE` | 증분 워터마크 상태 파일 (기본 `.flopi_db_sync_state.json`) |
| `ALLOW_DELETE` | 삭제 동기화(`--delete`) 허용 여부 (기본 `false` = 차단) |
| `SYNC_SEQUENCE` | 동기화 후 PK 시퀀스 자동 보정 여부 (기본 `false`) |
| `INSERT_ONLY` | PK 중복 무시·신규만 INSERT 여부 (기본 `false`) |
| `ADD_COLUMNS` | 원본 신규 컬럼을 PG에 자동 `ALTER ADD COLUMN` 여부 (기본 `false`) |

## 동작 방식
1. 원본 SQLite 를 **읽기전용(`mode=ro`)** 으로 열어 `PRAGMA table_info` 로 컬럼·PK 를
   읽는다. (원본은 어떤 경우에도 변경되지 않음)
2. PG `information_schema` 로 대상 컬럼 타입을 읽어 **안전 변환**한다
   (boolean ← 0/1, bytea ← bytes 등 / 컬럼 누락·테이블 부재 시 사전 차단).
   `--add-columns` 시 원본에만 있는 **신규 컬럼은 `ALTER ADD COLUMN`**(SQLite→PG
   타입 자동 매핑: `DATETIME→timestamp`, `INT→bigint` 등)으로 PG에 추가한다.
   (테이블 생성·삭제·타입변경은 하지 않고 경고만)
3. PG에 대해 `INSERT ... ON CONFLICT (pk) DO UPDATE SET ... WHERE 값이 다를 때만`
   단일 문으로 upsert 한다. (변경된 컬럼이 있는 행만 UPDATE, 불필요한 쓰기 방지)
4. `--incremental` 시 워터마크 컬럼 `>= 직전값` 행만 읽어 전송하고, 커밋 성공 후
   새 최댓값을 `STATE_FILE` 에 저장한다. (`>=` 라 같은 시각 행도 누락되지 않음)
5. `--delete` 시 원본 PK 전체를 임시 테이블(COPY)에 적재한 뒤 anti-join `DELETE`
   로 PG의 잉여행을 제거한다. (upsert·삭제가 같은 트랜잭션 내에서 원자적으로 반영)
6. PK 시퀀스(SERIAL/IDENTITY)를 **항상 탐지**해 로그로 알리고, `--sync-sequence`
   (또는 `SYNC_SEQUENCE=true`) 시 동기화 후 시퀀스를 최댓값으로 보정한다.
7. 테이블 단위로 트랜잭션 커밋. 한 테이블 실패가 다른 테이블에 영향 없음.

## 운영 사용 방법

### 1) 최초 설치 (서버 1회)
```bash
# 소스 배치
cd /opt/flopi-sync                      # 예시 설치 경로

# (인증 필요 시) Nexus 자격증명 등록
export UV_INDEX_NEXUS_USERNAME="..."; export UV_INDEX_NEXUS_PASSWORD="..."

uv sync                                  # Nexus에서 의존성 설치 + .venv 생성
cp .env.example .env && vi .env          # 접속정보 입력
```

`.env` 운영값 예시:
```dotenv
SQLITE_PATH=/path/to/source/simulator.db
PG_HOST=<PG_호스트_또는_VIP>
PG_PORT=5432
PG_USER=<계정>
PG_PASSWORD=********
PG_DB=<데이터베이스명>
PG_SCHEMA=<스키마>
BATCH_SIZE=1000
STATE_FILE=/opt/flopi-sync/.flopi_db_sync_state.json
ALLOW_DELETE=false        # --delete 허용 시 true
SYNC_SEQUENCE=false       # 시퀀스 자동 보정 시 true
```

### 2) 수동 실행
```bash
cd /opt/flopi-sync
uv run flopi-sync <테이블명>                              # 전체 upsert
uv run flopi-sync <테이블명> --incremental updated_at     # 증분
uv run flopi-sync <테이블명> --incremental updated_at --delete  # 증분+삭제 미러
```

### 3) 정기 실행 (cron)
`.env` / 워터마크 파일 경로가 절대경로인지 확인하고, 작업 디렉터리를 고정한다.
```cron
# 매일 02:00 주요 테이블 증분 동기화 (로그 적재)
0 2 * * *  cd /opt/flopi-sync && /usr/local/bin/uv run flopi-sync users orders \
           --incremental updated_at >> /var/log/flopi-sync.log 2>&1
```
> cron은 사용자 PATH/환경변수를 상속하지 않으므로 `uv` 절대경로를 쓰고,
> 인증·TLS 환경변수가 필요하면 cron 스크립트 안에서 `export` 한다.

### 4) 종료 코드 (배치/모니터링 연동)
| 코드 | 의미 |
|------|------|
| `0` | 전체 테이블 정상 동기화 |
| `1` | 일부 테이블 동기화 실패 (로그에 테이블별 사유 출력, 나머지는 계속 진행) |
| `2` | 설정 오류 (필수 환경변수 누락 등) — 실행 전 중단 |

### 5) 운영 팁
- **첫 증분 실행**은 워터마크가 없어 전체를 한 번 읽는다(이후부터 변경분만). 대량 테이블은 최초 1회 `--full-refresh`로 기준선을 잡는 것을 권장.
- **워터마크 꼬임/재적재**가 필요하면 `--full-refresh` 또는 `STATE_FILE` 삭제 후 재실행.
- **삭제 미러(`--delete`)**는 기본 차단되어 있다. 사용하려면 `.env` 에 `ALLOW_DELETE=true` 를 설정해야 하며(미설정 시 종료코드 `2`로 중단), 원본 PK 전체를 비교하므로 원본이 일부만 들어온 상태(부분 적재)에서 실행하면 정상 데이터가 삭제될 수 있다 → 원본 적재 완료 후 실행.
- 여러 테이블에 FK 의존성이 있으면 **부모 테이블 먼저** 인자 순서를 잡는다.
- **시퀀스 기반 PK + 앱이 PG에 직접 INSERT** 하는 테이블은 `--sync-sequence`(또는
  `SYNC_SEQUENCE=true`)로 보정해야 이후 nextval 충돌을 막는다.
- **원본에 컬럼이 추가**되면 `--add-columns` 로 PG에 자동 반영된다. 단 **증분 모드에선
  기존 미변경 행이 NULL로 남으므로**, 컬럼 추가 직후 **1회 전체 동기화**(증분 없이)로
  백필한 뒤 평소 증분으로 돌리는 것을 권장. 적용 전 `--migrate-dry-run` 으로 DDL 확인 가능.

## 전제 조건
- SQLite 테이블에 **PRIMARY KEY가 정의**되어 있어야 한다.
- `SQLITE_PATH` 는 **파일명까지 포함한 실제 파일 경로**여야 한다(없으면 에러로 중단).
- PG 대상 테이블이 **동일한 컬럼·PK 제약**으로 미리 존재해야 한다
  (`ON CONFLICT` 가 동작하려면 PG에 PK/UNIQUE 제약 필요). 도구는 테이블을 생성(DDL)하지 않는다.
- 컬럼명은 SQLite와 PG가 동일하다고 가정한다.
- 원본 SQLite 는 읽기전용으로 열려 **변경되지 않는다**.

## 부가: FastAPI 서비스 제어 스크립트
[`service-template/`](service-template/) 에 여러 FastAPI 프로젝트에서 재사용 가능한
`start.sh` / `stop.sh` / `status.sh` 템플릿이 있다(이 동기화 도구와는 별개). 자세한 내용은
해당 폴더의 README 참고.
