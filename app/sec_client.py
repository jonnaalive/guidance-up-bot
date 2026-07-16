from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .models import Filing

ATOM = {"a": "http://www.w3.org/2005/Atom"}
GUIDANCE_RE = re.compile(
    r"\b(guidance|outlook|forecast|raises?|raised|raising|revises?|updated?\s+(?:annual|full[- ]year)\s+outlook)\b",
    re.IGNORECASE,
)
FINANCIAL_RE = re.compile(
    r"\b(revenue|sales|earnings|EPS|margin|EBITDA|income|cash flow|capex|capital expenditures)\b",
    re.IGNORECASE,
)


class SecClient:
    def __init__(self, user_agent: str, *, timeout: int = 25) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Host": "www.sec.gov",
            }
        )
        self.timeout = timeout
        self._last_request = 0.0
        self._tickers: dict[str, str] | None = None

    def _get(self, url: str) -> requests.Response:
        elapsed = time.monotonic() - self._last_request
        if elapsed < 0.12:
            time.sleep(0.12 - elapsed)
        response = self.session.get(url, timeout=self.timeout)
        self._last_request = time.monotonic()
        response.raise_for_status()
        return response

    def ticker_map(self) -> dict[str, str]:
        if self._tickers is None:
            data = self._get("https://www.sec.gov/files/company_tickers.json").json()
            self._tickers = {}
            for row in data.values():
                cik = str(row["cik_str"])
                self._tickers.setdefault(cik, row["ticker"].upper())
        return self._tickers

    def latest_filings(
        self,
        forms: tuple[str, ...],
        count: int,
        *,
        pages: int = 1,
        lookback_hours: int = 24,
    ) -> list[Filing]:
        filings: dict[str, Filing] = {}
        tickers = self.ticker_map()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        for form in forms:
            for page in range(pages):
                url = (
                    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
                    f"&type={form}&owner=include&count={count}&start={page * count}&output=atom"
                )
                root = ET.fromstring(self._get(url).content)
                entries = root.findall("a:entry", ATOM)
                if not entries:
                    break
                page_has_recent = False
                for entry in entries:
                    filed_at = entry.findtext("a:updated", default="", namespaces=ATOM)
                    try:
                        filed_datetime = datetime.fromisoformat(filed_at)
                    except ValueError:
                        continue
                    if filed_datetime < cutoff:
                        continue
                    page_has_recent = True
                    title = entry.findtext("a:title", default="", namespaces=ATOM)
                    summary = entry.findtext("a:summary", default="", namespaces=ATOM)
                    link_node = entry.find("a:link", ATOM)
                    filing_url = link_node.attrib.get("href", "") if link_node is not None else ""
                    accession = self._accession(filing_url)
                    cik_match = re.search(r"CIK:\s*0*(\d+)", summary)
                    if not cik_match:
                        cik_match = re.search(r"\(0*(\d{7,10})\)", title)
                    cik = str(int(cik_match.group(1))) if cik_match else ""
                    company = re.sub(r"^.*? - ", "", title).strip() or title
                    company = re.sub(r"\s+\(\d{7,10}\)\s+\([^)]+\)\s*$", "", company)
                    if not accession or not filing_url:
                        continue
                    filings[accession] = Filing(
                        accession=accession,
                        form=form,
                        company=company,
                        cik=cik,
                        ticker=tickers.get(cik, ""),
                        filed_at=filed_at,
                        filing_url=filing_url,
                    )
                if not page_has_recent:
                    break
        return sorted(filings.values(), key=lambda item: item.filed_at)

    @staticmethod
    def _accession(url: str) -> str:
        match = re.search(r"(\d{10}-\d{2}-\d{6})", url)
        if match:
            return match.group(1)
        match = re.search(r"accession_number=([\d-]+)", url)
        return match.group(1) if match else ""

    def hydrate(self, filing: Filing) -> Filing:
        index_html = self._get(filing.filing_url).text
        soup = BeautifulSoup(index_html, "html.parser")
        choices: list[tuple[int, str]] = []
        for row in soup.select("table.tableFile tr"):
            cells = row.find_all("td")
            link = row.find("a", href=True)
            if len(cells) < 4 or not link:
                continue
            doc_type = cells[3].get_text(" ", strip=True).upper()
            href = link["href"]
            if not href.lower().endswith((".htm", ".html", ".txt")):
                continue
            score = 0 if doc_type.startswith("EX-99") else 1 if doc_type == filing.form else 9
            choices.append((score, urljoin(filing.filing_url, href)))
        if not choices:
            return filing
        text_parts: list[str] = []
        selected_url = ""
        for _, document_url in sorted(choices)[:3]:
            html = self._get(document_url).text
            text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
            if GUIDANCE_RE.search(text) and FINANCIAL_RE.search(text):
                selected_url = selected_url or document_url
                text_parts.append(self.guidance_context(text))
        filing.document_url = selected_url
        filing.text = "\n\n".join(text_parts)[:24_000]
        return filing

    @staticmethod
    def guidance_context(text: str, *, radius: int = 2500, max_chars: int = 24_000) -> str:
        snippets = []
        seen = set()
        for match in GUIDANCE_RE.finditer(text):
            start = max(0, match.start() - radius)
            end = min(len(text), match.end() + radius)
            snippet = text[start:end].strip()
            if not FINANCIAL_RE.search(snippet):
                continue
            fingerprint = snippet[:200]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            snippets.append(snippet)
            if sum(len(item) for item in snippets) >= max_chars:
                break
        return "\n\n[GUIDANCE CONTEXT]\n\n".join(snippets)[:max_chars]

    @staticmethod
    def is_candidate(text: str) -> bool:
        return bool(GUIDANCE_RE.search(text) and FINANCIAL_RE.search(text))






