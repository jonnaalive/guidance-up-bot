# Guidance Up Bot

## 2026-09-05 운영 개선

- SEC GET은 timeout·429·5xx를 최대 3회 재시도한다. 페이지 실패는 다른 페이지와 공시 처리를 막지 않으며 부분 실패는 Actions 실패로 표시한다.
- CIK·회계기간·지표·정규화한 금액별로 알림을 중복 제거한다. 기존 notified_at 기록을 새 식별자로 마이그레이션하므로 배포 후 과거 알림을 재전송하지 않는다.
- `guidance_up`, `new_guidance`, `one_off_guidance`, `turnaround_candidate`를 구분한다. 신규 전망을 일괄 상향으로 export하던 오류를 수정했다.
- 턴어라운드는 원문에서 같은 기준의 연속 3개 분기를 확인할 수 있을 때 EPS·영업마진·FCF·순부채 중 최소 2개 지표의 반전을 요구한다. 자료가 부족하면 판정을 보류한다. 별도 과거 재무 수집기나 전 종목 3분기 데이터베이스가 있는 것은 아니다. 추출 숫자·기간·근거는 코드에서 다시 검증하며, 일회성 근거가 있으면 턴어라운드에서 제외한다.
- `screening-state` 브랜치에 DB를, `digest-state` 브랜치에 종합보고 발송·완료·보류 이력을 저장한다. 캐시는 보조 수단이다. 최초 마이그레이션에 기존 캐시마저 없으면 seed를 재발송하지 않고 실패한다.
- Discord가 명확히 실패 응답을 보내면 다음 실행에 재시도한다. 타임아웃처럼 수신 여부를 알 수 없는 경우에는 `uncertain`으로 보류한다. 관리자가 수신을 확인해 상태를 정리해야 한다. 네트워크 경계에서 완전한 exactly-once를 보장하지 않는다.
- 운영 장애는 GitHub Actions 실패로 확인한다. 별도 장애 Discord 채널은 구성돼 있지 않으므로 알림을 임의로 추가하지 않았다.

### 이미 분석했거나 잠시 보류할 종목

사용자는 Codex에게 `CIEN 분석 완료`, `CIEN 30일 보류`, `CIEN 다시 열어줘`라고 요청할 수 있다. Codex는 아래 관리 워크플로를 실행해 **저장 성공까지 확인**한다. 이 명령은 Discord 메시지를 보내지 않는다.

```sh
gh workflow run signal-hub.yml --repo jonnaalive/guidance-up-bot -f review_ticker=CIEN -f review_status=done
gh workflow run signal-hub.yml --repo jonnaalive/guidance-up-bot -f review_ticker=CIEN -f review_status=snoozed
gh workflow run signal-hub.yml --repo jonnaalive/guidance-up-bot -f review_ticker=CIEN -f review_status=reopen
```

`review_until`을 주면 완료 기준 시각 또는 보류 종료일로 사용한다. 생략 시 완료는 현재, 보류는 30일이다. 완료 시점까지의 사건은 조사 권유에서 제외하고 이후 새 사건은 `new-factory-update` 검토로 분류한다. 종목 영구 차단은 아니다.

Discord 연결은 발신 전용 웹훅이다. Discord 안에 쓴 답장을 읽거나 슬래시 명령을 받는 봇 계정은 아직 연결돼 있지 않다.

### 발송 없는 운영 점검

```sh
gh workflow run hourly.yml --repo jonnaalive/guidance-up-bot -f dry_run=true
python -m pytest -q
```

GitHub 예약 실행에는 지연이 생길 수 있다. cron은 매시간이지만 정시 실행을 보장하지 않는다.

SEC의 최신 8-K·6-K·10-Q·10-K 공시를 스캔해 회사가 직전 가이던스보다 수치 전망을 올렸는지 판정하고 Discord로 알립니다.

## 판정 방식

1. SEC 최신 8-K·6-K·10-Q·10-K 공시 피드를 수집합니다.
2. 보도자료(EX-99)와 본문에서 가이던스 및 재무 지표 문구가 함께 있는 후보만 추립니다.
3. Gemini Structured Outputs가 매출, EPS, 마진, EBITDA, CAPEX 등의 현재·이전 범위를 추출합니다.
4. 현재 공시에 이전 수치가 함께 있으면 즉시 비교합니다. 없으면 SQLite에 저장한 해당 회사의 이전 가이던스와 비교합니다.
5. 가이던스 증가 폭, 수주 가시성, 산업 촉매, 실행 리스크를 바탕으로 Pick 점수와 선정 이유를 만듭니다.
6. 신뢰도 기준을 넘은 상향만 Discord Webhook으로 전송합니다.

SEC 원문은 무료이고 실시간에 가깝지만, 회사마다 공시 방식이 달라 완전한 탐지는 보장하지 않습니다. 최초 실행 전 과거 가이던스가 없더라도 공시에 ‘기존 범위에서 신규 범위로 상향’이 명시된 경우는 바로 탐지합니다.

## 로컬 실행

```powershell
cd guidance-discord-bot
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`에 아래 세 값을 입력합니다.

- `DISCORD_WEBHOOK_URL`: Discord 채널 설정 → 연동 → Webhook → 새 Webhook → URL 복사
- `GEMINI_API_KEY`: Google AI Studio에서 발급한 Gemini API 키
- `SEC_USER_AGENT`: SEC 정책상 연락 가능한 이름과 이메일

연결 및 1회 스캔:

```powershell
python main.py --test-discord
python main.py --once
```

Discord 전송 없이 확인하려면 `python main.py --once --dry-run`을 사용합니다. 계속 실행하려면 `python main.py`를 실행합니다.

## Railway 배포

1. 이 폴더를 새 GitHub 저장소에 올리고 Railway에서 Deploy from GitHub repo를 선택합니다.
2. `.env.example`의 환경 변수를 Railway Variables에 등록합니다.
3. SQLite 보존을 위해 Railway Volume을 `/data`에 마운트하고 `DATA_DIR=/data`로 설정합니다.
4. Deploy 후 로그에서 `스캔 완료`를 확인합니다.

## 운영 옵션

- 전체 미국 상장사: `WATCHLIST`를 비워 둡니다.
- 특정 종목: `WATCHLIST=AAPL,MSFT,NVDA`
- 알림 기준 강화: `MIN_CONFIDENCE=0.90`
- 호출량 축소: `FEED_COUNT=50`, `POLL_MINUTES=30`

같은 회사·기간·지표·신규 범위가 8-K와 10-K 등에 중복 게재돼도 한 번만 알립니다. Discord 전송 실패 건은 다음 주기에 재시도합니다.






## GitHub Actions 시간별 실행

`.github/workflows/hourly.yml`은 매시간 17분에 최신 SEC 실적 공시를 한 번 스크리닝합니다. `GEMINI_API_KEY`, `DISCORD_WEBHOOK_URL`, `SEC_USER_AGENT`를 GitHub Actions Secrets에 저장해야 합니다. SQLite 상태는 Actions Cache에 이어 저장해 같은 공시와 8-K·10-K 중복 알림을 막습니다. Actions 지연이나 캐시 만료 시 실행 시각 또는 중복 방지 상태가 달라질 수 있습니다.
