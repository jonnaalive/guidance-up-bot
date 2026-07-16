from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    discord_webhook_url: str
    gemini_api_key: str
    gemini_model: str
    sec_user_agent: str
    data_dir: Path
    poll_minutes: int
    feed_count: int
    forms: tuple[str, ...]
    watchlist: frozenset[str]
    min_confidence: float
    dry_run: bool = False

    @classmethod
    def from_env(cls, *, dry_run: bool = False) -> "Settings":
        load_dotenv()
        watchlist = frozenset(
            item.strip().upper()
            for item in os.getenv("WATCHLIST", "").split(",")
            if item.strip()
        )
        settings = cls(
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip(),
            sec_user_agent=os.getenv("SEC_USER_AGENT", "").strip(),
            data_dir=Path(os.getenv("DATA_DIR", "./data")),
            poll_minutes=max(5, int(os.getenv("POLL_MINUTES", "15"))),
            feed_count=min(100, max(10, int(os.getenv("FEED_COUNT", "100")))),
            forms=tuple(
                item.strip().upper()
                for item in os.getenv("FORMS", "8-K,6-K,10-Q,10-K").split(",")
                if item.strip()
            ),
            watchlist=watchlist,
            min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.80")),
            dry_run=dry_run,
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        return settings

    def validate(self, *, require_discord: bool = True) -> None:
        missing = []
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if not self.sec_user_agent or "@" not in self.sec_user_agent:
            missing.append("SEC_USER_AGENT (예: Name email@example.com)")
        if require_discord and not self.dry_run and not self.discord_webhook_url:
            missing.append("DISCORD_WEBHOOK_URL")
        if missing:
            raise ValueError("필수 환경 변수가 없습니다: " + ", ".join(missing))



