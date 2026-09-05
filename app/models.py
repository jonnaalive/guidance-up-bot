from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class TrendPoint(BaseModel):
    period: str = Field(description="Explicit fiscal quarter, e.g. FY2026 Q1")
    value: float
    evidence: str = Field(description="Exact source excerpt containing this quarterly value")


class TurnaroundTrend(BaseModel):
    metric: Literal["eps", "operating_margin", "fcf", "net_debt"]
    unit: str
    points: list[TrendPoint] = Field(default_factory=list, description="Three consecutive fiscal quarters, oldest first, same basis/unit. Never invent missing quarters.")


class GuidanceMetric(BaseModel):
    metric: str = Field(description="Revenue, adjusted EPS, margin, CAPEX 등 지표명")
    period: str = Field(description="가이던스 대상 기간")
    unit: str = Field(description="USD, USD/share, %, etc.")
    current_low: float | None = None
    current_high: float | None = None
    previous_low: float | None = None
    previous_high: float | None = None
    direction: str = Field(description="raised/lowered/maintained/introduced/withdrawn/unknown")
    evidence: str = Field(description="판정 근거가 되는 짧은 원문")


class GuidanceAnalysis(BaseModel):
    has_guidance: bool
    is_raised: bool
    is_strong_new_guidance: bool = False
    confidence: float = Field(ge=0, le=1)
    summary_ko: str
    metrics: list[GuidanceMetric] = Field(default_factory=list)
    pick_score: int = Field(default=0, ge=0, le=100)
    pick_reason_ko: str = ""
    catalysts_ko: list[str] = Field(default_factory=list)
    risks_ko: list[str] = Field(default_factory=list)
    one_off_evidence: str = Field(default="", description="Exact excerpt attributing the guidance increase to a nonrecurring tax benefit, refund, asset sale or similar. Empty if absent.")
    turnaround_trends: list[TurnaroundTrend] = Field(default_factory=list)
    validated_turnaround_metrics: list[str] = Field(default_factory=list)

    @property
    def is_turnaround(self) -> bool:
        # Populated only by deterministic evidence validation, not model flags.
        return len(set(self.validated_turnaround_metrics)) >= 2 and not self.one_off_evidence

    @property
    def signal_type(self) -> str:
        if self.one_off_evidence:
            return "one_off_guidance"
        if self.is_turnaround:
            return "turnaround_candidate"
        return "guidance_up" if self.is_raised else "new_guidance"


class Filing(BaseModel):
    accession: str
    form: str
    company: str
    cik: str
    ticker: str = ""
    filed_at: str
    filing_url: str
    document_url: str = ""
    text: str = ""
