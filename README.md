# Guidance Up Bot

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
