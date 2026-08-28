from __future__ import annotations

import logging

from .analyzer import GuidanceAnalyzer
from .config import Settings
from .discord_client import DiscordNotifier
from .sec_client import SecClient
from .store import Store

logger = logging.getLogger(__name__)


class GuidanceService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sec = SecClient(settings.sec_user_agent)
        self.analyzer = GuidanceAnalyzer(settings.gemini_api_key, settings.gemini_model)
        self.store = Store(settings.data_dir / "guidance.db")
        self.notifier = DiscordNotifier(settings.discord_webhook_url) if settings.discord_webhook_url else None

    def run_once(self) -> dict[str, int]:
        stats = {"fetched": 0, "new": 0, "candidates": 0, "raised": 0, "strong_new": 0, "notified": 0}
        filings = self.sec.latest_filings(
            self.settings.forms,
            self.settings.feed_count,
            pages=self.settings.feed_pages,
            lookback_hours=self.settings.lookback_hours,
            earnings_only=self.settings.earnings_only,
        )
        stats["fetched"] = len(filings)
        for filing in filings:
            if self.store.seen(filing.accession):
                continue
            if self.settings.watchlist and filing.ticker not in self.settings.watchlist:
                continue
            stats["new"] += 1
            try:
                filing = self.sec.hydrate(filing)
                if not self.sec.is_candidate(filing.text):
                    self.store.save(filing, None)
                    continue
                stats["candidates"] += 1
                prior = self.store.latest_guidance(filing.ticker)
                analysis = self.analyzer.analyze(filing, prior)
                self.store.save(filing, analysis)
                if analysis.confidence >= self.settings.min_confidence:
                    if analysis.is_raised:
                        stats["raised"] += 1
                    elif analysis.is_strong_new_guidance:
                        stats["strong_new"] += 1
            except Exception:
                logger.exception("공시 처리 실패: %s %s", filing.ticker, filing.accession)

        for row in self.store.pending_alerts(self.settings.min_confidence):
            if self.settings.dry_run:
                logger.info("DRY RUN 알림: %s %s", row["ticker"], row["accession"])
                continue
            try:
                assert self.notifier is not None
                self.notifier.send(row)
                self.store.mark_notified(row["accession"])
                stats["notified"] += 1
            except Exception:
                logger.exception("Discord 전송 실패: %s", row["accession"])
        self.store.export_latest_signals(
            self.settings.data_dir / "latest_signals.json",
            self.settings.min_confidence,
        )
        return stats



