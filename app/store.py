from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .models import Filing, GuidanceAnalysis


class Store:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS filings (
                accession TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                company TEXT NOT NULL,
                form TEXT NOT NULL,
                filed_at TEXT NOT NULL,
                filing_url TEXT NOT NULL,
                document_url TEXT NOT NULL,
                analysis_json TEXT,
                is_raised INTEGER NOT NULL DEFAULT 0,
                is_actionable INTEGER NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 0,
                processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                notified_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_filings_ticker_date
                ON filings(ticker, filed_at DESC);
            CREATE TABLE IF NOT EXISTS sent_alerts (
                fingerprint TEXT PRIMARY KEY,
                accession TEXT NOT NULL,
                notified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(filings)")
        }
        if "is_actionable" not in columns:
            self.connection.execute(
                "ALTER TABLE filings ADD COLUMN is_actionable INTEGER NOT NULL DEFAULT 0"
            )
            self.connection.execute("UPDATE filings SET is_actionable = is_raised")
            self.connection.commit()

    def seen(self, accession: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM filings WHERE accession = ?", (accession,)
        ).fetchone()
        return row is not None

    def save(self, filing: Filing, analysis: GuidanceAnalysis | None) -> None:
        payload = analysis.model_dump_json() if analysis else None
        actionable = bool(
            analysis and (analysis.is_raised or analysis.is_strong_new_guidance)
        )
        self.connection.execute(
            """INSERT OR REPLACE INTO filings
            (accession, ticker, company, form, filed_at, filing_url, document_url,
             analysis_json, is_raised, is_actionable, confidence, notified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT notified_at FROM filings WHERE accession = ?), NULL))""",
            (
                filing.accession,
                filing.ticker,
                filing.company,
                filing.form,
                filing.filed_at,
                filing.filing_url,
                filing.document_url,
                payload,
                int(bool(analysis and analysis.is_raised)),
                int(actionable),
                analysis.confidence if analysis else 0,
                filing.accession,
            ),
        )
        self.connection.commit()

    def latest_guidance(self, ticker: str) -> GuidanceAnalysis | None:
        if not ticker:
            return None
        row = self.connection.execute(
            """SELECT analysis_json FROM filings
            WHERE ticker = ? AND analysis_json IS NOT NULL
            ORDER BY filed_at DESC LIMIT 1""",
            (ticker,),
        ).fetchone()
        return GuidanceAnalysis.model_validate_json(row["analysis_json"]) if row else None

    def pending_alerts(self, min_confidence: float) -> list[sqlite3.Row]:
        rows = self.connection.execute(
            """SELECT * FROM filings
            WHERE is_actionable = 1 AND confidence >= ? AND notified_at IS NULL
            ORDER BY filed_at""",
            (min_confidence,),
        )
        pending = []
        batch_fingerprints: set[str] = set()
        for row in rows:
            fingerprint = self._fingerprint(row)
            already_sent = self.connection.execute(
                "SELECT 1 FROM sent_alerts WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            if already_sent or fingerprint in batch_fingerprints:
                continue
            batch_fingerprints.add(fingerprint)
            pending.append(row)
        return pending

    def mark_notified(self, accession: str) -> None:
        row = self.connection.execute(
            "SELECT * FROM filings WHERE accession = ?", (accession,)
        ).fetchone()
        if row:
            self.connection.execute(
                "INSERT OR IGNORE INTO sent_alerts (fingerprint, accession) VALUES (?, ?)",
                (self._fingerprint(row), accession),
            )
        self.connection.execute(
            "UPDATE filings SET notified_at = CURRENT_TIMESTAMP WHERE accession = ?",
            (accession,),
        )
        self.connection.commit()

    @staticmethod
    def _fingerprint(row: sqlite3.Row) -> str:
        analysis = GuidanceAnalysis.model_validate_json(row["analysis_json"])
        actionable_metrics = [
            {
                "metric": metric.metric.lower().strip(),
                "period": metric.period.lower().strip(),
                "unit": metric.unit.lower().strip(),
                "low": metric.current_low,
                "high": metric.current_high,
            }
            for metric in analysis.metrics
            if metric.direction in {"raised", "introduced"}
        ]
        raw = json.dumps(
            {"issuer": row["ticker"] or row["company"], "metrics": actionable_metrics},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()
