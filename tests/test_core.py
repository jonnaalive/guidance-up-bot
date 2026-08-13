from pathlib import Path

from app.analyzer import GuidanceAnalyzer
from app.discord_client import DiscordNotifier
from app.models import Filing, GuidanceAnalysis, GuidanceMetric
from app.sec_client import SecClient
from app.store import Store


def sample_analysis() -> GuidanceAnalysis:
    return GuidanceAnalysis(
        has_guidance=True,
        is_raised=True,
        confidence=0.94,
        summary_ko="연간 매출 가이던스를 상향했습니다.",
        pick_score=88,
        pick_reason_ko="수주 가시성과 가이던스 증가 폭이 큽니다.",
        catalysts_ko=["AI 고객사 양산 진입"],
        risks_ko=["고객 집중도"],
        metrics=[
            GuidanceMetric(
                metric="Revenue",
                period="FY2026",
                unit="USD million",
                current_low=1200,
                current_high=1250,
                previous_low=1100,
                previous_high=1150,
                direction="raised",
                evidence="raises revenue guidance to $1.20-$1.25 billion",
            )
        ],
    )


def sample_filing() -> Filing:
    return Filing(
        accession="0000000000-26-000001",
        form="8-K",
        company="Example Inc.",
        cik="1",
        ticker="EXM",
        filed_at="2026-07-16T12:00:00-04:00",
        filing_url="https://www.sec.gov/example",
        document_url="https://www.sec.gov/example/ex991.htm",
    )


def test_candidate_filter_requires_guidance_and_metric():
    assert SecClient.is_candidate("We raise full-year revenue guidance.")
    assert not SecClient.is_candidate("Revenue increased in the prior quarter.")
    assert not SecClient.is_candidate("We expect customer satisfaction to improve.")


def test_guidance_context_is_bounded_and_keeps_financial_sentence():
    text = "x" * 10_000 + " raises full-year revenue guidance to $150 million " + "y" * 10_000
    context = SecClient.guidance_context(text, radius=200, max_chars=500)
    assert "revenue guidance" in context
    assert len(context) <= 500


def test_hallucinated_raise_evidence_is_rejected():
    analysis = sample_analysis()
    filing_text = "Revenue is expected to be between $1.2 and $1.3 billion."
    checked = GuidanceAnalyzer._validate_evidence(analysis, filing_text)
    assert not checked.is_raised
    assert checked.metrics[0].direction == "unknown"
    assert checked.confidence <= 0.49


def test_explicit_raise_with_source_evidence_is_accepted():
    analysis = sample_analysis()
    evidence = analysis.metrics[0].evidence
    checked = GuidanceAnalyzer._validate_evidence(analysis, evidence)
    assert checked.is_raised


def test_strong_new_guidance_is_actionable(tmp_path: Path):
    evidence = "Aehr expects FY2027 revenue of $130 million to $150 million"
    analysis = GuidanceAnalysis(
        has_guidance=True,
        is_raised=False,
        is_strong_new_guidance=True,
        confidence=0.95,
        summary_ko="FY2027 신규 매출 가이던스를 제시했습니다.",
        metrics=[
            GuidanceMetric(
                metric="Revenue",
                period="FY2027",
                unit="USD million",
                current_low=130,
                current_high=150,
                direction="introduced",
                evidence=evidence,
            )
        ],
        pick_score=92,
        pick_reason_ko="높은 성장률과 수주 가시성이 확인됩니다.",
    )
    source = evidence + ", representing 160%-200% year-over-year growth."
    checked = GuidanceAnalyzer._validate_evidence(analysis, source)
    assert checked.is_strong_new_guidance
    store = Store(tmp_path / "strong.db")
    filing = sample_filing().model_copy(update={"text": source})
    store.save(filing, checked)
    row = store.pending_alerts(0.8)[0]
    embed = DiscordNotifier.build_payload(row)["embeds"][0]
    assert embed["title"] == "🚀 강한 신규 가이던스 기업"


def test_store_pending_and_mark_notified(tmp_path: Path):
    store = Store(tmp_path / "test.db")
    store.save(sample_filing(), sample_analysis())
    assert store.seen("0000000000-26-000001")
    assert store.latest_guidance("EXM").is_raised
    assert len(store.pending_alerts(0.8)) == 1
    store.mark_notified("0000000000-26-000001")
    assert store.pending_alerts(0.8) == []


def test_cross_form_duplicate_guidance_only_alerts_once(tmp_path: Path):
    store = Store(tmp_path / "test.db")
    first = sample_filing()
    second = first.model_copy(
        update={"accession": "0000000000-26-000002", "form": "10-K"}
    )
    store.save(first, sample_analysis())
    store.save(second, sample_analysis())
    assert len(store.pending_alerts(0.8)) == 1


def test_pick_message_payload(tmp_path: Path):
    store = Store(tmp_path / "test.db")
    store.save(sample_filing(), sample_analysis())
    row = store.pending_alerts(0.8)[0]
    embed = DiscordNotifier.build_payload(row)["embeds"][0]
    assert embed["title"] == "📈 가이던스 상향 기업"
    assert "7월의 Pick: EXM" in embed["fields"][0]["name"]
    assert any(field["name"] == "선정 이유" for field in embed["fields"])
    assert any(field["name"] == "확인할 리스크" for field in embed["fields"])


def test_range_formatting():
    assert DiscordNotifier._range(10, 12, "USD") == "10–12 USD"
    assert DiscordNotifier._range(None, None, "%") == "이전 수치 미상"





