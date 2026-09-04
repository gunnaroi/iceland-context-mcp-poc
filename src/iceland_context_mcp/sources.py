from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .models import EeaField, EeaResult, LawResult, Provenance, SourceRecord

REGISTRY_PATH = Path(__file__).with_name("source_registry.json")
USER_AGENT = "IcelandTrustedContextMCPPoC/0.1 (+public research proof of concept)"
MAX_TEXT_CHARS = 30_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_registry() -> dict[str, dict]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def registry_records() -> list[SourceRecord]:
    return [SourceRecord(key=k, **v) for k, v in load_registry().items()]


def registry_record(key: str) -> SourceRecord:
    data = load_registry()
    if key not in data:
        raise ValueError(f"Unknown source key: {key}. Known keys: {', '.join(sorted(data))}")
    return SourceRecord(key=key, **data[key])


def _clean_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    return text[:MAX_TEXT_CHARS]


def _extract_fields(soup: BeautifulSoup) -> list[EeaField]:
    fields: list[EeaField] = []
    seen: set[tuple[str, str]] = set()
    for row in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
        if len(cells) >= 2:
            label = cells[0].strip()
            value = " | ".join(c.strip() for c in cells[1:] if c.strip())
            if label and value and (label, value) not in seen:
                seen.add((label, value))
                fields.append(EeaField(label=label, value=value))
    return fields[:150]


async def _get(url: str) -> httpx.Response:
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "is,en;q=0.8"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > 5_000_000:
            raise ValueError("Source response exceeded PoC safety limit (5 MB).")
        return response


def normalize_celex(celex: str) -> str:
    value = re.sub(r"\s+", "", celex).upper()
    if not re.fullmatch(r"[0-9][0-9A-Z()_-]{7,24}", value):
        raise ValueError("CELEX identifier has an unexpected format.")
    return value


async def fetch_law(year: int, number: int) -> LawResult:
    if not 1800 <= year <= datetime.now().year + 1:
        raise ValueError("Unexpected law year.")
    if not 1 <= number <= 999:
        raise ValueError("Law number must be between 1 and 999.")
    official_identifier = f"{year}{number:03d}"
    url = f"https://www.althingi.is/lagas/nuna/{official_identifier}.html"
    response = await _get(url)
    soup = BeautifulSoup(response.text, "lxml")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else None
    text = _clean_text(soup)
    source = registry_record("althingi_lagasafn")
    return LawResult(
        official_identifier=official_identifier,
        title=title,
        text=text,
        provenance=Provenance(
            publisher=source.publisher,
            source_url=str(response.url),
            retrieved_at=utc_now(),
            authority_class=source.authority_class,
            authority_label=source.authority_label,
        ),
    )


async def fetch_ees(celex: str) -> EeaResult:
    celex = normalize_celex(celex)
    url = f"https://gagnagrunnur.ees.is/{celex.lower()}"
    response = await _get(url)
    soup = BeautifulSoup(response.text, "lxml")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else None
    source = registry_record("ees_gagnagrunnur")
    return EeaResult(
        celex=celex,
        title=title,
        fields=_extract_fields(soup),
        extracted_text=_clean_text(soup),
        provenance=Provenance(
            publisher=source.publisher,
            source_url=str(response.url),
            retrieved_at=utc_now(),
            authority_class=source.authority_class,
            authority_label=source.authority_label,
            warning=source.notes,
        ),
    )


async def fetch_efta(celex: str) -> EeaResult:
    celex = normalize_celex(celex)
    url = f"https://www.efta.int/eea-lex/{celex.lower()}"
    response = await _get(url)
    soup = BeautifulSoup(response.text, "lxml")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else None
    source = registry_record("efta_eea_lex")
    return EeaResult(
        celex=celex,
        title=title,
        fields=_extract_fields(soup),
        extracted_text=_clean_text(soup),
        provenance=Provenance(
            publisher=source.publisher,
            source_url=str(response.url),
            retrieved_at=utc_now(),
            authority_class=source.authority_class,
            authority_label=source.authority_label,
            warning=source.notes,
        ),
    )
