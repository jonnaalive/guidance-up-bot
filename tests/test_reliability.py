import json
from datetime import datetime, timezone
from unittest.mock import Mock

import requests

from app.analyzer import GuidanceAnalyzer
from app.models import TrendPoint, TurnaroundTrend
from app.sec_client import SecClient
from app.store import Store
from test_core import sample_analysis, sample_filing


def test_metric_order_alias_and_unit_do_not_realert(tmp_path):
    store = Store(tmp_path / "state.db")
    first = sample_filing()
    store.save(first, sample_analysis())
    store.mark_notified(first.accession)
    analysis = sample_analysis()
    metric = analysis.metrics[0]
    metric.metric = "Net sales"
    metric.unit = "USD billion"
    metric.current_low, metric.current_high = 1.2, 1.25
    metric.period = "Full year 2026"
    store.save(first.model_copy(update={"accession": "another", "form": "10-K"}), analysis)
    assert not store.pending_alerts(0.8)
    metric.current_high = 1.30
    store.save(first.model_copy(update={"accession": "genuinely-new"}), analysis)
    assert len(store.pending_alerts(0.8)) == 1


def test_migrate_legacy_sent_records_without_replay(tmp_path):
    path = tmp_path / "state.db"
    store = Store(path)
    first = sample_filing()
    store.save(first, sample_analysis())
    store.mark_notified(first.accession)
    store.connection.execute("DELETE FROM migrations")
    store.connection.execute("DELETE FROM sent_metrics")
    store.connection.commit()
    store.connection.close()
    restored = Store(path)
    restored.save(first.model_copy(update={"accession": "other-form"}), sample_analysis())
    assert not restored.pending_alerts(0.8)


def test_uncertain_delivery_is_held_until_review(tmp_path):
    store = Store(tmp_path / "state.db")
    filing = sample_filing()
    store.save(filing, sample_analysis())
    store.record_delivery(filing.accession, "sending")
    assert not store.pending_alerts(0.8)
    store.record_delivery(filing.accession, "uncertain")
    assert not store.pending_alerts(0.8)
    store.record_delivery(filing.accession, "failed")
    assert len(store.pending_alerts(0.8)) == 1


def test_export_dedup_and_new_guidance_type(tmp_path):
    store = Store(tmp_path / "state.db")
    filing = sample_filing().model_copy(update={"filed_at": datetime.now(timezone.utc).isoformat()})
    analysis = sample_analysis().model_copy(update={"is_raised": False, "is_strong_new_guidance": True})
    analysis.metrics[0].direction = "introduced"
    store.save(filing, analysis)
    store.save(filing.model_copy(update={"accession": "another"}), analysis)
    out = tmp_path / "signals.json"
    store.export_latest_signals(out, 0.8)
    signals = json.loads(out.read_text())
    assert len(signals) == 1
    assert signals[0]["signal"] == "new_guidance"
    assert signals[0]["event_id"]


def test_partial_feed_failure_continues_other_pages(monkeypatch):
    sec = SecClient("Test test@example.com")
    monkeypatch.setattr(sec, "ticker_map", lambda: {"1": "EXM"})
    stamp = datetime.now(timezone.utc).isoformat()
    xml = f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><updated>{stamp}</updated><title>10-Q - Example (0000000001)</title><summary>CIK: 1</summary><link href="https://www.sec.gov/0000000001-26-000001-index.htm"/></entry></feed>'''
    calls = []
    def get(url):
        calls.append(url)
        if "type=8-K" in url:
            raise requests.Timeout()
        return Mock(content=xml.encode())
    monkeypatch.setattr(sec, "_get", get)
    filings = sec.latest_filings(("8-K", "10-Q"), 100)
    assert len(filings) == 1
    assert sec.feed_errors == ["8-K:page0"]
    assert len(calls) == 2


def test_material_lowering_blocks_raise():
    analysis = sample_analysis()
    lowered = analysis.metrics[0].model_copy(update={"metric": "EPS", "direction": "lowered"})
    analysis.metrics.append(lowered)
    assert not GuidanceAnalyzer._validate_evidence(analysis, analysis.metrics[0].evidence).is_raised


def test_two_verified_three_quarter_reversals_and_one_off_gate():
    analysis = sample_analysis()
    source = analysis.metrics[0].evidence
    for metric in ["eps", "operating_margin"]:
        points = [TrendPoint(period=f"FY2026 Q{i}", value=v, evidence=f"{metric} Q{i}: {v}") for i, v in enumerate([1, -2, 3], 1)]
        analysis.turnaround_trends.append(TurnaroundTrend(metric=metric, unit="same", points=points))
        source += " " + " ".join(p.evidence for p in points)
    validated = GuidanceAnalyzer._validate_evidence(analysis, source)
    assert validated.is_turnaround
    # Validated state survives SQLite JSON serialization.
    assert type(analysis).model_validate_json(validated.model_dump_json()).is_turnaround
    analysis.one_off_evidence = "Guidance raised because of a tax refund"
    source += " " + analysis.one_off_evidence
    checked = GuidanceAnalyzer._validate_evidence(analysis, source)
    assert not checked.is_turnaround
    assert checked.signal_type == "one_off_guidance"
    analysis.turnaround_trends[0].points[2].value = 999
    checked = GuidanceAnalyzer._validate_evidence(analysis, source)
    assert "eps" not in checked.validated_turnaround_metrics
