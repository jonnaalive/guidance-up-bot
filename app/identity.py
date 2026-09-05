"""Stable financial identities, independent of generated summaries and list order."""
import hashlib
import json
import re


def canonical_metric(metric):
    name = re.sub(r"[^a-z0-9%]+", " ", metric.metric.lower()).strip()
    aliases = {"sales": "revenue", "net sales": "revenue", "net revenue": "revenue",
        "revenues": "revenue", "earnings per share": "eps", "free cash flow": "fcf",
        "capital expenditures": "capex", "capital expenditure": "capex"}
    name = aliases.get(name, name)
    name = name.replace("non gaap", "adjusted")
    name = name.replace("earnings per share", "eps")
    unit = metric.unit.lower().replace("$", "usd")
    scale = 1_000_000_000 if re.search(r"\bbillions?\b|\bbn\b", unit) else 1_000_000 if re.search(r"\bmillions?\b|\bmm\b", unit) else 1_000 if re.search(r"\bthousands?\b", unit) else 1
    unit = re.sub(r"\b(billions?|millions?|thousands?|bn|mm)\b", "", unit)
    unit = re.sub(r"\s+", "", unit).replace("pershare", "/share")
    period = metric.period.lower()
    years = re.findall(r"20\d{2}", period)
    quarter = re.search(r"q([1-4])|([1-4])q", period)
    if years:
        period = years[0] + ("q" + next(g for g in quarter.groups() if g) if quarter else "fy" if re.search(r"fy|fiscal|full.year|annual", period) else re.sub(r"\s+", "", period))
    else:
        period = re.sub(r"\s+", "", period)
    def number(value):
        return None if value is None else round(float(value) * scale, 8)
    return {"metric": name, "period": period, "unit": unit,
        "low": number(metric.current_low), "high": number(metric.current_high)}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def metric_ids(row, analysis):
    cik = str(row["cik"]) if "cik" in row.keys() and row["cik"] else ""
    if not cik:
        match = re.search(r"/data/0*(\d+)/", row["filing_url"])
        cik = match.group(1) if match else ""
    issuer = cik.lstrip("0") or row["ticker"] or row["company"]
    result = set()
    for metric in analysis.metrics:
        if metric.direction not in {"raised", "introduced"}:
            continue
        key = canonical_metric(metric)
        # Missing numbers cannot identify the same economic event indefinitely.
        if key["low"] is None and key["high"] is None:
            key["date"] = row["filed_at"][:10]
        result.add(digest([issuer, key]))
    if analysis.is_turnaround:
        for trend in analysis.turnaround_trends:
            if trend.metric in analysis.validated_turnaround_metrics:
                result.add(digest([issuer, "turnaround", trend.metric, trend.unit,
                    [(p.period, p.value) for p in trend.points]]))
    return result or {digest([issuer, row["accession"]])}
