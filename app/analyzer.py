from __future__ import annotations

import re
import time

from google import genai
from google.genai import errors, types

from .models import Filing, GuidanceAnalysis

SYSTEM_PROMPT = """You analyze issuer-authored SEC filing text for changes to company guidance.
Only treat forward-looking, company-issued quantitative financial guidance as guidance.
Do not treat analyst estimates, historical results, market commentary, or dividend changes as guidance.
Mark is_raised=true only when at least one comparable guidance metric is raised and no material metric is lowered.
A raised range includes a higher midpoint, a higher lower bound with unchanged upper bound, or explicit issuer language that guidance was raised.
If the filing explicitly states both old and new values, use those. Otherwise compare against PRIOR_GUIDANCE when supplied.
Numbers must preserve the source scale and unit. If evidence is ambiguous, return unknown and lower confidence.
Write summary_ko in concise Korean and include the new range and the prior range when known.
Score pick_score from 0 to 100 using only filing evidence: guidance increase magnitude 30, revenue visibility such as backlog or signed orders 25, concrete industry/customer catalysts 25, and risk-adjusted execution quality 20.
Write pick_reason_ko as a persuasive but factual Korean investment thesis in 3-5 sentences. Populate catalysts_ko and risks_ko with concrete items.
Never claim that guidance beat market expectations unless analyst consensus is explicitly included in the supplied text. Never use unsupported superlatives. Evidence must be a short exact excerpt from the supplied filing."""

SYSTEM_PROMPT += """
New annual guidance is introduced, not raised relative to last year's guidance.
Compare only identical fiscal periods, metric definitions, accounting bases and units.
For introduced guidance set is_strong_new_guidance only for explicit >=30% forward year-over-year growth tied to that guidance metric.
Identify one-off drivers using one_off_evidence; distinguish a refund-driven increase from recurring operating improvement.
Extract turnaround_trends only when three consecutive fiscal-quarter actual values of EPS, operating margin, FCF or net debt are explicitly available on the same basis. Use separate exact evidence for each value. Do not use annual totals, forecasts or reconstruct absent quarters. validated_turnaround_metrics must be empty; code computes it.
"""


class GuidanceAnalyzer:
    def __init__(self, api_key: str, model: str, *, min_interval: float = 5.0) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.min_interval = min_interval
        self._last_request = 0.0

    def analyze(self, filing: Filing, prior: GuidanceAnalysis | None) -> GuidanceAnalysis:
        prior_json = prior.model_dump_json() if prior else "없음"
        prompt = (
            f"회사: {filing.company} ({filing.ticker or 'ticker unknown'})\n"
            f"공시 유형/일자: {filing.form} / {filing.filed_at}\n"
            f"PRIOR_GUIDANCE:\n{prior_json}\n\n"
            f"CURRENT_FILING:\n{filing.text}"
        )
        response = None
        for attempt in range(2):
            self._pace()
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_json_schema=GuidanceAnalysis.model_json_schema(),
                        temperature=0.1,
                    ),
                )
                break
            except errors.ClientError as exc:
                if "429" not in str(exc) or attempt == 1:
                    raise
                time.sleep(65)
        if response is None or not response.text:
            raise RuntimeError("Gemini가 구조화된 분석 결과를 반환하지 않았습니다.")
        analysis = GuidanceAnalysis.model_validate_json(response.text)
        return self._validate_evidence(analysis, filing.text)

    @staticmethod
    def _validate_evidence(
        analysis: GuidanceAnalysis, filing_text: str
    ) -> GuidanceAnalysis:
        source = re.sub(r"\s+", " ", filing_text).lower()
        valid_raise = False
        explicit_raise = re.compile(
            r"\b(raise[sd]?|raising|increase[sd]?\s+(?:its\s+)?(?:annual\s+|full[- ]year\s+)?guidance|up\s+from|compared\s+to\s+(?:the\s+)?previous)\b",
            re.IGNORECASE,
        )
        for metric in analysis.metrics:
            if metric.direction != "raised":
                continue
            evidence = re.sub(r"\s+", " ", metric.evidence).strip().lower()
            evidence_exists = bool(evidence and evidence in source)
            has_comparison = (
                metric.previous_low is not None or metric.previous_high is not None
            )
            previous = [v for v in (metric.previous_low, metric.previous_high) if v is not None]
            current = [v for v in (metric.current_low, metric.current_high) if v is not None]
            invalid_range = any(low is not None and high is not None and low > high for low, high in [(metric.current_low, metric.current_high), (metric.previous_low, metric.previous_high)])
            if invalid_range or (previous and current and sum(current) / len(current) <= sum(previous) / len(previous)):
                metric.direction = "unknown"
                continue
            if evidence_exists and (has_comparison or explicit_raise.search(evidence)):
                valid_raise = True
            else:
                metric.direction = "unknown"
        analysis.is_raised = analysis.is_raised and valid_raise and not any(m.direction == "lowered" for m in analysis.metrics)
        analysis.is_strong_new_guidance = (
            analysis.is_strong_new_guidance
            and any(
                metric.direction == "introduced"
                and re.sub(r"\s+", " ", metric.evidence).strip().lower() in source
                and GuidanceAnalyzer._has_strong_yoy_growth(GuidanceAnalyzer._evidence_context(source, metric.evidence))
                for metric in analysis.metrics
                if metric.evidence.strip()
            )
        )
        one_off = re.sub(r"\s+", " ", analysis.one_off_evidence).strip().lower()
        analysis.one_off_evidence = analysis.one_off_evidence if one_off and one_off in source else ""
        analysis.validated_turnaround_metrics = []
        for trend in analysis.turnaround_trends:
            if len(trend.points) != 3:
                continue
            periods = []
            valid = True
            for point in trend.points:
                period = re.fullmatch(r"(?:FY)?(20\d{2})\s*Q([1-4])", point.period, re.I)
                evidence = re.sub(r"\s+", " ", point.evidence).strip().lower()
                numbers = re.findall(r"-?\d+(?:\.\d+)?", evidence.replace(",", "").replace("(", "-").replace(")", ""))
                if not period or not evidence or evidence not in source or point.value not in [float(n) for n in numbers]:
                    valid = False
                    break
                periods.append(int(period[1]) * 4 + int(period[2]))
            if not valid or periods != list(range(periods[0], periods[0] + 3)):
                continue
            a, b, c = [p.value for p in trend.points]
            improving = (b >= a and c < b) if trend.metric == "net_debt" else ((b <= a and c > b) or (a < 0 and a < b < c))
            if improving:
                analysis.validated_turnaround_metrics.append(trend.metric)
        if not (analysis.is_raised or analysis.is_strong_new_guidance or analysis.is_turnaround):
            analysis.confidence = min(analysis.confidence, 0.49)
        return analysis

    @staticmethod
    def _evidence_context(source: str, evidence: str) -> str:
        evidence = re.sub(r"\s+", " ", evidence).strip().lower()
        start = source.find(evidence)
        return source[start:start + len(evidence) + 200] if start >= 0 else ""

    @staticmethod
    def _has_strong_yoy_growth(source: str) -> bool:
        for match in re.finditer(r"year[- ]over[- ]year\s+growth", source):
            context = source[max(0, match.start() - 120) : match.end() + 120]
            percentages = [
                float(value)
                for value in re.findall(r"(\d+(?:\.\d+)?)\s*%", context)
            ]
            if percentages and max(percentages) >= 30:
                return True
        return False

    def _pace(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
