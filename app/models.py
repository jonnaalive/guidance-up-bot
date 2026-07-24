from __future__ import annotations

from pydantic import BaseModel, Field


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


