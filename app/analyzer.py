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
            if evidence_exists and (has_comparison or explicit_raise.search(evidence)):
                valid_raise = True
            else:
                metric.direction = "unknown"
        analysis.is_raised = analysis.is_raised and valid_raise
        analysis.is_strong_new_guidance = (
            analysis.is_strong_new_guidance
            and GuidanceAnalyzer._has_strong_yoy_growth(source)
            and any(
                metric.direction == "introduced"
                and re.sub(r"\s+", " ", metric.evidence).strip().lower() in source
                for metric in analysis.metrics
                if metric.evidence.strip()
            )
        )
        if not (analysis.is_raised or analysis.is_strong_new_guidance):
            analysis.confidence = min(analysis.confidence, 0.49)
        return analysis

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


