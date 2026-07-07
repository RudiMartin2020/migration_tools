# 범용 FastAPI 서비스 제어 스크립트

`start.sh` / `stop.sh` / `status.sh` 세 개의 **독립 스크립트**로 FastAPI 서비스를 제어한다.
별도 설정 파일 없이, **각 스크립트 상단의 설정 영역만** 프로젝트에 맞게 수정한다.

## 구성
| 파일 | 역할 |
|------|------|
| `start.sh` | 서비스 시작 (`APP_NAME`, `CMD` 수정) |
| `stop.sh` | 서비스 중지 (`APP_NAME` 만) |
| `status.sh` | 상태 확인 (`APP_NAME` 만, 실행중 rc=0 / 중지 rc=1) |

## 사용법
```bash
# 1) 세 파일을 대상 프로젝트 폴더에 복사
cp start.sh stop.sh status.sh /opt/myapp/
cd /opt/myapp

# 2) 각 스크립트 상단의 설정 영역 수정
#    - start.sh : APP_NAME, CMD
#    - stop.sh / status.sh : APP_NAME (start.sh 와 동일하게)
vi start.sh stop.sh status.sh

# 3) 실행 권한
chmod +x start.sh stop.sh status.sh

# 4) 제어
./start.sh
./status.sh
./stop.sh
```

## 설정 영역 예시 (start.sh)
```bash
# ===== 프로젝트별 설정 (여기만 수정) =====
APP_NAME="my-fastapi"
CMD="uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"
```

> ⚠️ `APP_NAME` 은 세 스크립트가 **같은 값**이어야 한다 (같은 PID 파일을 참조).

## 파일 관리
- PID 파일: `run/${APP_NAME}.pid`
- 로그 파일: `log/${APP_NAME}.log`
- `run/` `log/` 폴더는 `start.sh` 실행 시 자동 생성된다.

## 동작
- 백그라운드 실행 + `run/` 에 PID 관리, `log/` 에 로그 적재
- `setsid` 로 **프로세스 그룹** 단위 실행 → stop 시 자식(워커)까지 함께 종료
- stop 은 TERM(최대 10초 대기) 후 미종료 시 KILL 로 강제 종료
- 어느 위치에서 실행해도 스크립트 폴더 기준으로 동작 (cron 안전)

## 비고
- Windows에서 편집/전송 시 줄바꿈 오류(`\r`)가 나면: `sed -i 's/\r$//' *.sh`
