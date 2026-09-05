from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .models import Filing, GuidanceAnalysis
from .identity import digest, metric_ids


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
        if "cik" not in columns:
            self.connection.execute("ALTER TABLE filings ADD COLUMN cik TEXT NOT NULL DEFAULT ''")
        self.connection.execute("CREATE TABLE IF NOT EXISTS sent_metrics (identity TEXT PRIMARY KEY)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS delivery_attempts (accession TEXT PRIMARY KEY, status TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY)")
        if not self.connection.execute("SELECT 1 FROM migrations WHERE name='metric-v2'").fetchone():
            for row in self.connection.execute("SELECT * FROM filings WHERE notified_at IS NOT NULL AND analysis_json IS NOT NULL").fetchall():
                analysis = GuidanceAnalysis.model_validate_json(row["analysis_json"])
                self.connection.executemany("INSERT OR IGNORE INTO sent_metrics VALUES (?)", [(key,) for key in metric_ids(row, analysis)])
            self.connection.execute("INSERT INTO migrations VALUES ('metric-v2')")
        self.connection.commit()

    def seen(self, accession: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM filings WHERE accession = ?", (accession,)
        ).fetchone()
        return row is not None

    def save(self, filing: Filing, analysis: GuidanceAnalysis | None) -> None:
        payload = analysis.model_dump_json() if analysis else None
        actionable = bool(
            analysis and (analysis.is_raised or analysis.is_strong_new_guidance or analysis.is_turnaround)
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

        self.connection.execute("UPDATE filings SET cik=? WHERE accession=?", (filing.cik, filing.accession))
        self.connection.commit()

    def latest_guidance(self, ticker: str, before: str | None = None) -> GuidanceAnalysis | None:
        if not ticker:
            return None
        rows = self.connection.execute(
            """SELECT analysis_json FROM filings
            WHERE ticker = ? AND analysis_json IS NOT NULL AND (? IS NULL OR datetime(filed_at) < datetime(?))
            ORDER BY datetime(filed_at) DESC""",
            (ticker, before, before),
        )
        for row in rows:
            analysis = GuidanceAnalysis.model_validate_json(row["analysis_json"])
            if analysis.has_guidance and analysis.metrics:
                return analysis
        return None

    def pending_alerts(self, min_confidence: float) -> list[sqlite3.Row]:
        rows = self.connection.execute(
            """SELECT * FROM filings
            WHERE is_actionable = 1 AND confidence >= ? AND notified_at IS NULL
              AND accession NOT IN (SELECT accession FROM delivery_attempts WHERE status IN ('sending', 'uncertain'))
            ORDER BY filed_at""",
            (min_confidence,),
        )
        pending = []
        batch_fingerprints: set[str] = set()
        covered = {row[0] for row in self.connection.execute("SELECT identity FROM sent_metrics")}
        for held in self.connection.execute("SELECT f.* FROM filings f JOIN delivery_attempts d USING (accession) WHERE d.status IN ('sending', 'uncertain') AND f.analysis_json IS NOT NULL"):
            covered.update(metric_ids(held, GuidanceAnalysis.model_validate_json(held["analysis_json"])))
        for row in rows:
            keys = metric_ids(row, GuidanceAnalysis.model_validate_json(row["analysis_json"]))
            if keys <= covered:
                continue
            fingerprint = self._fingerprint(row)
            already_sent = self.connection.execute(
                "SELECT 1 FROM sent_alerts WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            if already_sent or fingerprint in batch_fingerprints:
                continue
            batch_fingerprints.add(fingerprint)
            covered.update(keys)
            pending.append(row)
        return pending

    def record_delivery(self, accession: str, status: str) -> None:
        self.connection.execute("INSERT OR REPLACE INTO delivery_attempts VALUES (?, ?)", (accession, status))
        self.connection.commit()

    def export_latest_signals(self, path: Path, min_confidence: float) -> None:
        rows = self.connection.execute(
            """SELECT * FROM filings
            WHERE is_actionable = 1 AND confidence >= ?
              AND datetime(filed_at) >= datetime('now', '-7 days')
            ORDER BY filed_at DESC""",
            (min_confidence,),
        ).fetchall()
        signals = []
        covered = set()
        for row in rows:
            analysis = GuidanceAnalysis.model_validate_json(row["analysis_json"])
            keys = metric_ids(row, analysis)
            if keys <= covered:
                continue
            covered.update(keys)
            signals.append({
                "event_id": self._fingerprint(row),
                "ticker": row["ticker"],
                "company": row["company"],
                "detected_at": row["filed_at"],
                "source_bot": "guidance",
                "signal": analysis.signal_type,
                "summary": analysis.summary_ko,
                "source_url": row["filing_url"],
                "confidence": analysis.confidence,
            })
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(signals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def mark_notified(self, accession: str) -> None:
        row = self.connection.execute(
            "SELECT * FROM filings WHERE accession = ?", (accession,)
        ).fetchone()
        if row:
            self.connection.executemany("INSERT OR IGNORE INTO sent_metrics VALUES (?)",
                [(key,) for key in metric_ids(row, GuidanceAnalysis.model_validate_json(row["analysis_json"]))])
            self.connection.execute(
                "INSERT OR IGNORE INTO sent_alerts (fingerprint, accession) VALUES (?, ?)",
                (self._fingerprint(row), accession),
            )
        self.connection.execute(
            "UPDATE filings SET notified_at = CURRENT_TIMESTAMP WHERE accession = ?",
            (accession,),
        )
        self.connection.commit()

        self.record_delivery(accession, "sent")

    @staticmethod
    def _fingerprint(row: sqlite3.Row) -> str:
        analysis = GuidanceAnalysis.model_validate_json(row["analysis_json"])
        return digest(sorted(metric_ids(row, analysis)))

    @staticmethod
    def legacy_fingerprint(row: sqlite3.Row) -> str:
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
