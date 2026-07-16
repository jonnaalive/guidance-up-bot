from __future__ import annotations

from google import genai
from google.genai import types

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
    def __init__(self, api_key: str, model: str) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def analyze(self, filing: Filing, prior: GuidanceAnalysis | None) -> GuidanceAnalysis:
        prior_json = prior.model_dump_json() if prior else "없음"
        prompt = (
            f"회사: {filing.company} ({filing.ticker or 'ticker unknown'})\n"
            f"공시 유형/일자: {filing.form} / {filing.filed_at}\n"
            f"PRIOR_GUIDANCE:\n{prior_json}\n\n"
            f"CURRENT_FILING:\n{filing.text}"
        )
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
        if not response.text:
            raise RuntimeError("Gemini가 구조화된 분석 결과를 반환하지 않았습니다.")
        return GuidanceAnalysis.model_validate_json(response.text)
