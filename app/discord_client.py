from __future__ import annotations

from datetime import datetime

import requests

from .models import GuidanceAnalysis


class DiscordNotifier:
    def __init__(self, webhook_url: str, *, timeout: int = 20) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send(self, row) -> None:
        response = requests.post(
            self.webhook_url + ("&" if "?" in self.webhook_url else "?") + "wait=true",
            json=self.build_payload(row),
            timeout=self.timeout,
        )
        response.raise_for_status()

    @classmethod
    def build_payload(cls, row) -> dict:
        analysis = GuidanceAnalysis.model_validate_json(row["analysis_json"])
        ticker_label = f" ({row['ticker']})" if row["ticker"] else ""
        company_label = f"{row['company']}{ticker_label}"
        metric_lines = []
        for metric in analysis.metrics[:8]:
            if metric.direction != "raised":
                continue
            old = cls._range(metric.previous_low, metric.previous_high, metric.unit)
            new = cls._range(metric.current_low, metric.current_high, metric.unit)
            metric_lines.append(f"• **{metric.metric} ({metric.period})**: {old} → {new}")

        description = f"• **{company_label}**: {analysis.summary_ko}"
        if metric_lines:
            description += "\n\n" + "\n".join(metric_lines)

        fields = [
            {
                "name": f"🏆 {cls._month_label(row['filed_at'])}의 Pick: {row['ticker'] or row['company']}",
                "value": f"**Pick 점수: {analysis.pick_score}/100**",
                "inline": False,
            },
            {
                "name": "선정 이유",
                "value": (analysis.pick_reason_ko or "공시에서 추가 투자 근거를 확인 중입니다.")[:1024],
                "inline": False,
            },
        ]
        if analysis.catalysts_ko:
            fields.append(
                {
                    "name": "핵심 촉매",
                    "value": cls._bullets(analysis.catalysts_ko),
                    "inline": False,
                }
            )
        if analysis.risks_ko:
            fields.append(
                {
                    "name": "확인할 리스크",
                    "value": cls._bullets(analysis.risks_ko),
                    "inline": False,
                }
            )
        fields.extend(
            [
                {"name": "공시", "value": f"{row['form']} · {row['filed_at'][:10]}", "inline": True},
                {"name": "판정 신뢰도", "value": f"{row['confidence']:.0%}", "inline": True},
            ]
        )
        title = (
            "🚀 강한 신규 가이던스 기업"
            if analysis.is_strong_new_guidance and not analysis.is_raised
            else "📈 가이던스 상향 기업"
        )
        return {
            "username": "Guidance Up Bot",
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": title,
                    "description": description[:4000],
                    "url": row["document_url"] or row["filing_url"],
                    "color": 0x2ECC71,
                    "fields": fields,
                    "footer": {"text": "SEC 원문 기반 자동 판정 · 투자 판단 전 원문 확인"},
                }
            ],
        }

    def send_test(self) -> None:
        response = requests.post(
            self.webhook_url,
            json={"content": "✅ Guidance Up Bot 연결 테스트 성공"},
            timeout=self.timeout,
        )
        response.raise_for_status()

    @staticmethod
    def _bullets(items: list[str]) -> str:
        return "\n".join(f"• {item}" for item in items)[:1024]

    @staticmethod
    def _month_label(value: str) -> str:
        try:
            return f"{datetime.fromisoformat(value).month}월"
        except (TypeError, ValueError):
            return "이번 달"

    @staticmethod
    def _range(low: float | None, high: float | None, unit: str) -> str:
        if low is None and high is None:
            return "이전 수치 미상"
        if high is None or low == high:
            return f"{low:g} {unit}"
        if low is None:
            return f"≤ {high:g} {unit}"
        return f"{low:g}–{high:g} {unit}"

