from __future__ import annotations

import base64
import io
import json
import re
import warnings
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pdfplumber
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# CELLAR/EUR-Lex serves XHTML; parsing it with BeautifulSoup's HTML parser (as
# every other adapter in this module does, deliberately, for a consistent
# get_text() pattern) is intentional here, not an oversight this warning should flag.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from .models import (
    BillDocument,
    BillDocumentResult,
    BillResult,
    CourtRulingResult,
    CourtRulingSearchHit,
    CourtRulingSearchResult,
    EeaField,
    EeaResult,
    EurLexActResult,
    LawBasisReference,
    LawResult,
    Provenance,
    RegulationAmendmentEvent,
    RegulationResult,
    RegulationSearchHit,
    RegulationSearchResult,
    RelatedMatter,
    SourceRecord,
    StjornartidindiAdvertResult,
    StjornartidindiSearchHit,
    StjornartidindiSearchResult,
)

REGISTRY_PATH = Path(__file__).with_name("source_registry.json")
USER_AGENT = "IcelandTrustedContextMCPPoC/0.1 (+public research proof of concept)"
MAX_TEXT_CHARS = 30_000

REGLUGERD_API = "https://api.reglugerd.is/api/v1"
ALTHINGI_XML = "https://www.althingi.is/altext/xml"
ISLAND_IS_GRAPHQL = "https://island.is/api/graphql"

# Icelandic regulations state their enabling statute in a "heimild"/"lagastoð"
# clause, e.g. "...sett samkvæmt heimild í ... laga nr. 136/2022 um landamæri..."
# or "...sett með heimild í 20. gr. laga um sviðslistir nr. 165/2019...". The law
# name/article can sit between "laga" and "nr.", so this matches across a bounded
# gap rather than requiring them adjacent.
LAW_BASIS_RE = re.compile(r"laga\w*\b(?:(?!nr\.).){0,150}?nr\.\s*(\d{1,4})\s*/\s*(\d{4})", re.IGNORECASE | re.DOTALL)


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
    # Some documents' own source HTML has literal newlines inside a single text
    # node — one word per line within one <p> (seen live in legacy Stjórnartíðindi
    # adverts) — so get_text() preserves those as real line breaks no matter what
    # separator is used. Mark real paragraph/block boundaries with a placeholder
    # distinct from any whitespace, collapse every actual newline in the source
    # text to a space, then turn only the placeholders back into line breaks.
    for tag in soup.find_all(["p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6"]):
        tag.insert_after("\x00")
    text = soup.get_text(" ").replace("\n", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = text.replace("\x00", "\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n".join(lines)[:MAX_TEXT_CHARS]


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


async def _get(url: str, max_bytes: int = 5_000_000) -> httpx.Response:
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "is,en;q=0.8"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > max_bytes:
            raise ValueError(f"Source response exceeded PoC safety limit ({max_bytes // 1_000_000} MB).")
        return response


# Legislative PDFs (fylgirit, large frumvörp) run well past the default 5 MB
# text/HTML cap — seen up to ~7.7 MB for a single fjárlög document.
PDF_MAX_BYTES = 25_000_000


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


CELLAR_SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
CELLAR_REST_BASE = "https://publications.europa.eu/resource/celex"

CELLAR_METADATA_QUERY = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT DISTINCT ?title ?dateDoc ?dateForce ?dateEnd ?inForce ?resType WHERE {{
  ?work cdm:resource_legal_id_celex "{celex}"^^xsd:string .
  ?expr cdm:expression_belongs_to_work ?work .
  ?expr cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/{lang3}> .
  ?expr cdm:expression_title ?title .
  OPTIONAL {{ ?work cdm:work_date_document ?dateDoc . }}
  OPTIONAL {{ ?work cdm:resource_legal_date_entry-into-force ?dateForce . }}
  OPTIONAL {{ ?work cdm:resource_legal_date_end-of-validity ?dateEnd . }}
  OPTIONAL {{ ?work cdm:resource_legal_in-force ?inForce . }}
  OPTIONAL {{
    ?work cdm:work_has_resource-type ?resTypeUri .
    ?resTypeUri skos:prefLabel ?resType .
    FILTER(lang(?resType) = "en")
  }}
}}
LIMIT 1
"""

# EUR-Lex/CELLAR uses ISO 639-2/B three-letter codes, not the ISO 639-1 codes
# used elsewhere in this file. Icelandic is deliberately absent: Iceland is not
# an EU member and EUR-Lex publishes no Icelandic-language expressions.
EUR_LEX_LANGUAGES = {"en": "ENG", "da": "DAN", "de": "DEU", "fr": "FRA", "sv": "SWE"}


async def fetch_eur_lex_act(celex: str, language: str = "en") -> EurLexActResult:
    celex = normalize_celex(celex)
    if language not in EUR_LEX_LANGUAGES:
        raise ValueError(f"Unsupported language {language!r}. Supported: {', '.join(sorted(EUR_LEX_LANGUAGES))}.")
    lang3 = EUR_LEX_LANGUAGES[language]

    query = CELLAR_METADATA_QUERY.format(celex=celex, lang3=lang3)
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        meta_response = await client.get(
            CELLAR_SPARQL_ENDPOINT,
            params={"query": query, "format": "application/sparql-results+json"},
        )
        meta_response.raise_for_status()
        bindings = meta_response.json()["results"]["bindings"]
    if not bindings:
        raise ValueError(f"No CELLAR expression found for CELEX {celex!r} in language {language!r}.")
    row = bindings[0]

    def _val(key: str) -> str | None:
        return row[key]["value"] if key in row else None

    end_of_validity = _val("dateEnd")
    if end_of_validity == "9999-12-31":
        end_of_validity = None  # CELLAR's sentinel for "no end date set", not a real date

    # CELLAR content-negotiates on Accept/Accept-Language: without an explicit
    # Accept for (x)html, it silently serves a different, much shorter metadata
    # representation instead of the full act text — no error, just wrong content.
    http_lang = {"en": "en", "da": "da", "de": "de", "fr": "fr", "sv": "sv"}[language]
    async with httpx.AsyncClient(
        timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        content_response = await client.get(
            f"{CELLAR_REST_BASE}/{celex}",
            headers={"Accept": "application/xhtml+xml, text/html", "Accept-Language": http_lang},
        )
        content_response.raise_for_status()
        if len(content_response.content) > PDF_MAX_BYTES:
            raise ValueError(f"CELLAR document exceeded PoC safety limit ({PDF_MAX_BYTES // 1_000_000} MB).")
    soup = BeautifulSoup(content_response.text, "lxml")
    text = _clean_text(soup)

    source = registry_record("eur_lex")
    return EurLexActResult(
        celex=celex,
        title=_val("title"),
        document_date=_val("dateDoc"),
        entry_into_force_date=_val("dateForce"),
        end_of_validity_date=end_of_validity,
        in_force=(_val("inForce") == "1") if _val("inForce") is not None else None,
        resource_type=_val("resType"),
        text=text,
        provenance=Provenance(
            publisher=source.publisher,
            source_url=str(content_response.url),
            retrieved_at=utc_now(),
            authority_class=source.authority_class,
            authority_label=source.authority_label,
        ),
    )


def _extract_law_basis(html_text: str) -> list[LawBasisReference]:
    plain = BeautifulSoup(html_text, "lxml").get_text(" ")
    plain = re.sub(r"\s+", " ", plain).strip()
    idx = plain.lower().rfind("heimild")
    window = plain[idx : idx + 400] if idx != -1 else plain[-500:]
    refs: list[LawBasisReference] = []
    seen: set[str] = set()
    for m in LAW_BASIS_RE.finditer(window):
        law_nr = f"{int(m.group(1))}/{m.group(2)}"
        if law_nr in seen:
            continue
        seen.add(law_nr)
        start = max(0, m.start() - 40)
        refs.append(LawBasisReference(law_nr=law_nr, context=window[start : m.end() + 20].strip()))
    return refs


def _regulation_identifier(number: int, year: int) -> str:
    if not 1 <= number <= 9999:
        raise ValueError("Regulation number must be between 1 and 9999.")
    if not 1800 <= year <= datetime.now().year + 1:
        raise ValueError("Unexpected regulation year.")
    return f"{number:04d}-{year}"


async def fetch_regulation(number: int, year: int, view: str = "current") -> RegulationResult:
    if view not in ("current", "original"):
        raise ValueError("view must be 'current' or 'original'.")
    identifier = _regulation_identifier(number, year)
    url = f"{REGLUGERD_API}/regulation/{identifier}/{view}/"
    response = await _get(url)
    data = response.json()
    if "text" not in data:
        redirect = data.get("redirectUrl", url)
        raise ValueError(
            f"Regulation {data.get('name', identifier)} predates the structured register; "
            f"only a legacy scan is available at {redirect}."
        )
    source = registry_record("reglugerdasafn")
    ministry = (data.get("ministry") or {}).get("name")
    return RegulationResult(
        official_identifier=data["name"],
        title=data["title"],
        view=view,
        text=data["text"][:MAX_TEXT_CHARS],
        ministry=ministry,
        signature_date=data.get("signatureDate"),
        published_date=data.get("publishedDate"),
        effective_date=data.get("effectiveDate"),
        repealed=bool(data.get("repealed", False)),
        last_amend_date=data.get("lastAmendDate"),
        law_chapters=[c.get("name", "") for c in data.get("lawChapters", []) if c.get("name")],
        history=[
            RegulationAmendmentEvent(
                date=h.get("date"),
                official_identifier=h.get("name", ""),
                title=h.get("title", ""),
                effect=h.get("effect", ""),
                status=h.get("status"),
            )
            for h in data.get("history", [])
        ],
        effects=[
            RegulationAmendmentEvent(
                date=e.get("date"),
                official_identifier=e.get("name", ""),
                title=e.get("title", ""),
                effect=e.get("effect", ""),
                status=e.get("status"),
            )
            for e in data.get("effects", [])
        ],
        law_basis=_extract_law_basis(data["text"]),
        original_doc_url=data.get("originalDoc"),
        provenance=Provenance(
            publisher=source.publisher,
            source_url=str(response.url),
            retrieved_at=utc_now(),
            authority_class=source.authority_class,
            authority_label=source.authority_label,
        ),
    )


async def search_regulations(query: str, limit: int = 10) -> RegulationSearchResult:
    if not query.strip():
        raise ValueError("Search query must not be empty.")
    limit = max(1, min(limit, 30))
    url = f"{REGLUGERD_API}/search"
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(
        timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept-Language": "is,en;q=0.8"}
    ) as client:
        # The upstream API accepts perPage but silently ignores it (always
        # returns its own fixed page size), so `limit` is enforced client-side.
        response = await client.get(url, params={"q": query})
        response.raise_for_status()
        data = response.json()
    return RegulationSearchResult(
        query=query,
        total_items=data.get("totalItems", 0),
        page=data.get("page", 1),
        hits=[
            RegulationSearchHit(
                official_identifier=item["name"],
                title=item["title"],
                published_date=item.get("publishedDate"),
                ministry=item.get("ministry"),
            )
            for item in data.get("data", [])[:limit]
        ],
    )


def _xml_text(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
    child = node.find(path)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


async def _get_xml(url: str) -> ET.Element:
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "is,en;q=0.8"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        # Parse from bytes: the document carries its own XML encoding
        # declaration and a decoded str input raises ValueError.
        return ET.fromstring(response.content)


async def current_parliament() -> int:
    root = await _get_xml(f"{ALTHINGI_XML}/loggjafarthing/yfirstandandi/")
    thing = root.find("þing")
    if thing is None or "númer" not in thing.attrib:
        raise ValueError("Could not resolve the current parliament (þing) number.")
    return int(thing.attrib["númer"])


async def fetch_bill(malnr: int, thing: int | None = None, malsflokkur: str = "A") -> BillResult:
    malsflokkur = malsflokkur.upper()
    if malsflokkur not in ("A", "B"):
        raise ValueError("malsflokkur must be 'A' or 'B'.")
    if thing is None:
        thing = await current_parliament()
    endpoint = "thingmal" if malsflokkur == "A" else "bmal"
    url = f"{ALTHINGI_XML}/thingmalalisti/{endpoint}/?lthing={thing}&malnr={malnr}"
    root = await _get_xml(url)
    mal = root.find("mál")
    if mal is None:
        raise ValueError(f"No matter found for þing {thing}, málsflokkur {malsflokkur}, málnr {malnr}.")

    subject_categories = [
        _xml_text(ef, "heiti") or ""
        for ef in root.findall("efnisflokkar/yfirflokkur/efnisflokkur")
        if _xml_text(ef, "heiti")
    ]
    rapporteurs = [
        _xml_text(f, "nafn") or "" for f in root.findall("framsögumenn/framsögumaður") if _xml_text(f, "nafn")
    ]
    related_matters = []
    for rel_mal in root.findall("tengdMál/skyltMál/mál"):
        rel_title = _xml_text(rel_mal, "málsheiti")
        if rel_title and "málsnúmer" in rel_mal.attrib and "þingnúmer" in rel_mal.attrib:
            related_matters.append(
                RelatedMatter(
                    thing=int(rel_mal.attrib["þingnúmer"]),
                    matter_number=int(rel_mal.attrib["málsnúmer"]),
                    title=rel_title,
                )
            )
    documents = []
    for skjal in root.findall("þingskjöl/þingskjal"):
        if "skjalsnúmer" not in skjal.attrib:
            continue
        slod = skjal.find("slóð")
        documents.append(
            BillDocument(
                document_number=int(skjal.attrib["skjalsnúmer"]),
                document_type=_xml_text(skjal, "skjalategund") or "",
                distributed_at=_xml_text(skjal, "útbýting"),
                html_url=_xml_text(slod, "html") if slod is not None else None,
                pdf_url=_xml_text(slod, "pdf") if slod is not None else None,
            )
        )

    source = registry_record("althingi_open_xml")
    return BillResult(
        thing=thing,
        matter_number=malnr,
        matter_class=malsflokkur,
        title=_xml_text(mal, "málsheiti") or "",
        matter_type=_xml_text(mal, "málstegund/heiti"),
        status=_xml_text(mal, "staðamáls"),
        subject_categories=subject_categories,
        rapporteurs=rapporteurs,
        related_matters=related_matters,
        documents=documents,
        provenance=Provenance(
            publisher=source.publisher,
            source_url=url,
            retrieved_at=utc_now(),
            authority_class=source.authority_class,
            authority_label=source.authority_label,
        ),
    )


# Alþingi's own "no inline text, see the PDF" marker on þingskjal HTML pages
# (verified live against a fjárlög document, which is PDF-only).
NO_INLINE_TEXT_MARKER = "til að skoða skjalið"
MIN_INLINE_TEXT_CHARS = 200


async def fetch_bill_document(thing: int, document_number: int) -> BillDocumentResult:
    html_url = f"https://www.althingi.is/altext/{thing}/s/{document_number:04d}.html"
    pdf_url = f"https://www.althingi.is/altext/pdf/{thing}/s/{document_number:04d}.pdf"
    response = await _get(html_url)
    soup = BeautifulSoup(response.text, "lxml")
    body = soup.find("body") or soup
    for tag in body(["script", "style", "nav"]):
        tag.decompose()
    plain = re.sub(r"\s+", " ", body.get_text(" ")).strip()

    # The page is a metadata/navigation shell (title, document list, breadcrumbs) even
    # when there's no inline text, so a marker check is more reliable than a raw length
    # cutoff alone, but both are used since the marker phrasing isn't guaranteed stable.
    has_inline_text = NO_INLINE_TEXT_MARKER.lower() not in plain.lower() and len(plain) > 2000

    if has_inline_text:
        text = _clean_text(soup)
        text_source = "html"
    else:
        pdf_response = await _get(pdf_url, max_bytes=PDF_MAX_BYTES)
        text = _pdf_bytes_to_text(pdf_response.content, MAX_TEXT_CHARS)
        text_source = "pdf"

    source = registry_record("althingi_open_xml")
    return BillDocumentResult(
        thing=thing,
        document_number=document_number,
        text=text,
        text_source=text_source,
        html_url=html_url,
        pdf_url=pdf_url,
        provenance=Provenance(
            publisher=source.publisher,
            source_url=html_url if text_source == "html" else pdf_url,
            retrieved_at=utc_now(),
            authority_class=source.authority_class,
            authority_label=source.authority_label,
        ),
    )


STJORNARTIDINDI_ADVERTS_QUERY = """
query($input: OfficialJournalOfIcelandAdvertsInput!) {
  officialJournalOfIcelandAdverts(input: $input) {
    adverts {
      id
      department { title slug }
      title
      publicationNumber { full }
      publicationDate
      involvedParty { title }
      type { title }
    }
    paging { totalItems }
  }
}
"""

STJORNARTIDINDI_ADVERT_QUERY = """
query($params: OfficialJournalOfIcelandAdvertSingleParams!) {
  officialJournalOfIcelandAdvert(params: $params) {
    advert {
      id
      department { title slug }
      title
      publicationNumber { full }
      publicationDate
      signatureDate
      involvedParty { title }
      type { title }
      categories { title }
      document { html }
    }
  }
}
"""


async def search_stjornartidindi(
    query: str | None = None,
    department: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 10,
) -> StjornartidindiSearchResult:
    limit = max(1, min(limit, 50))
    input_params: dict = {
        "search": query or "",
        "department": [department] if department else [],
        "category": [],
        "involvedParty": [],
        "mainType": [],
        "sortBy": "",
        "page": 1,
        "pageSize": limit,
    }
    # The upstream resolver 500s on an explicit dateFrom/dateTo: null rather than
    # treating it as "unbounded" — omit the keys entirely when unset (verified live).
    if date_from:
        input_params["dateFrom"] = date_from
    if date_to:
        input_params["dateTo"] = date_to
    variables = {"input": input_params}
    data = await _graphql(STJORNARTIDINDI_ADVERTS_QUERY, variables)
    result = data["officialJournalOfIcelandAdverts"]
    hits = [
        StjornartidindiSearchHit(
            id=item["id"],
            department=(item.get("department") or {}).get("slug", ""),
            title=item.get("title") or "",
            publication_number=(item.get("publicationNumber") or {}).get("full"),
            publication_date=item.get("publicationDate"),
            involved_party=(item.get("involvedParty") or {}).get("title"),
            advert_type=(item.get("type") or {}).get("title"),
        )
        for item in result["adverts"]
    ]
    return StjornartidindiSearchResult(query=query, total_items=result["paging"]["totalItems"], hits=hits)


async def fetch_stjornartidindi_advert(advert_id: str) -> StjornartidindiAdvertResult:
    data = await _graphql(STJORNARTIDINDI_ADVERT_QUERY, {"params": {"id": advert_id}})
    advert = (data.get("officialJournalOfIcelandAdvert") or {}).get("advert")
    if advert is None:
        raise ValueError(f"No Stjórnartíðindi advert found for id {advert_id!r}.")

    html = (advert.get("document") or {}).get("html") or ""
    soup = BeautifulSoup(html, "lxml")
    text = _clean_text(soup)

    source = registry_record("stjornartidindi")
    return StjornartidindiAdvertResult(
        id=advert["id"],
        department=(advert.get("department") or {}).get("slug", ""),
        title=advert.get("title") or "",
        publication_number=(advert.get("publicationNumber") or {}).get("full"),
        publication_date=advert.get("publicationDate"),
        signature_date=advert.get("signatureDate"),
        involved_party=(advert.get("involvedParty") or {}).get("title"),
        advert_type=(advert.get("type") or {}).get("title"),
        categories=[c["title"] for c in advert.get("categories", []) if c.get("title")],
        text=text,
        provenance=Provenance(
            publisher=source.publisher,
            source_url=f"https://island.is/stjornartidindi/{advert_id}",
            retrieved_at=utc_now(),
            authority_class=source.authority_class,
            authority_label=source.authority_label,
        ),
    )


def _court_authority(court: str) -> tuple[str, str]:
    normalized = court.strip().lower()
    if "hæstiréttur" in normalized:
        return "C1", "Official supreme court judgment — highest domestic precedent"
    if "landsréttur" in normalized:
        return "C2", "Official appellate court judgment"
    if "héraðsdóm" in normalized:
        return "C3", "Official first-instance court judgment"
    return "C", "Official Icelandic court judgment"


async def _graphql(query: str, variables: dict) -> dict:
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(
        timeout=timeout, headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    ) as client:
        response = await client.post(ISLAND_IS_GRAPHQL, json={"query": query, "variables": variables})
        response.raise_for_status()
        payload = response.json()
    if "errors" in payload:
        raise ValueError(f"island.is GraphQL error: {payload['errors']}")
    return payload["data"]


def _richtext_to_plain(node: dict) -> str:
    if node.get("nodeType") == "text":
        return node.get("value", "")
    joined = "".join(_richtext_to_plain(c) for c in node.get("content", []))
    if node.get("nodeType", "").startswith(("paragraph", "heading")):
        return joined + "\n\n"
    return joined


def _pdf_bytes_to_text(raw: bytes, max_chars: int) -> str:
    parts: list[str] = []
    total = 0
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            parts.append(page_text)
            total += len(page_text)
            if total >= max_chars:
                break
    return "\n\n".join(parts)[:max_chars]


def _pdf_base64_to_text(b64_data: str, max_chars: int) -> str:
    return _pdf_bytes_to_text(base64.b64decode(b64_data), max_chars)


VERDICTS_SEARCH_QUERY = """
query($input: WebVerdictsInput!) {
  webVerdicts(input: $input) {
    total
    items { id court caseNumber verdictDate title keywords }
  }
}
"""

VERDICT_BY_ID_QUERY = """
query($input: WebVerdictByIdInput!) {
  webVerdictById(input: $input) {
    item { title court caseNumber verdictDate keywords richText pdfString }
  }
}
"""


LAW_CITATION_RE = re.compile(r"^\s*(\d{1,4})\s*/\s*(\d{4})\s*$")

# Above this, a laws-filtered result is presumptively a silent-fallback rather
# than a real match set — verified live: an unrecognized article-level tag
# (e.g. "2018.90.1") returns ~31,000 of the ~43,000-total corpus instead of a
# real 0/near-0, while every whole-law query we tested stayed under a few
# hundred. This is a heuristic tripwire, not a proven threshold.
SUSPICIOUS_LAW_FILTER_TOTAL = 5000


def _to_law_citation_tag(law_citation: str) -> str:
    match = LAW_CITATION_RE.match(law_citation)
    if not match:
        raise ValueError(f"law_citation must look like 'NNN/YYYY' (e.g. '91/1991'), got {law_citation!r}.")
    number, year = match.groups()
    return f"{year}.{int(number)}"


async def search_court_rulings(
    query: str | None = None,
    court: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    law_citation: str | None = None,
    limit: int = 10,
) -> CourtRulingSearchResult:
    limit = max(1, min(limit, 30))
    laws = [_to_law_citation_tag(law_citation)] if law_citation else []
    variables = {
        "input": {
            "searchTerm": query,
            "court": [court] if court else [],
            "caseCategories": None,
            "caseTypes": None,
            "keywords": None,
            "caseContact": None,
            "caseNumber": None,
            "laws": laws,
            "dateFrom": date_from,
            "dateTo": date_to,
            "page": 1,
        }
    }
    data = await _graphql(VERDICTS_SEARCH_QUERY, variables)
    result = data["webVerdicts"]
    total = result["total"]
    hits = []
    for item in result["items"][:limit]:
        court_name = item.get("court") or ""
        authority_class, _ = _court_authority(court_name)
        hits.append(
            CourtRulingSearchHit(
                id=item["id"],
                court=court_name,
                case_number=item.get("caseNumber"),
                verdict_date=item.get("verdictDate"),
                title=item.get("title") or "",
                keywords=item.get("keywords") or [],
                authority_class=authority_class,
            )
        )
    suspicious = bool(law_citation) and total > SUSPICIOUS_LAW_FILTER_TOTAL
    return CourtRulingSearchResult(
        query=query,
        total_items=total,
        hits=hits,
        law_citation=law_citation,
        law_filter_suspicious=suspicious,
    )


async def fetch_court_ruling(ruling_id: str) -> CourtRulingResult:
    data = await _graphql(VERDICT_BY_ID_QUERY, {"input": {"id": ruling_id}})
    item = (data.get("webVerdictById") or {}).get("item")
    if item is None:
        raise ValueError(f"No court ruling found for id {ruling_id!r}.")

    if item.get("richText"):
        text = _richtext_to_plain(item["richText"]["document"]).strip()[:MAX_TEXT_CHARS]
        text_source = "richText"
    elif item.get("pdfString"):
        text = _pdf_base64_to_text(item["pdfString"], MAX_TEXT_CHARS)
        text_source = "pdf"
    else:
        text = ""
        text_source = "unavailable"

    registry_record("domar_island_is")  # validates the registry entry exists; raises loudly otherwise
    court_name = item.get("court") or ""
    authority_class, authority_label = _court_authority(court_name)
    return CourtRulingResult(
        id=ruling_id,
        court=court_name,
        case_number=item.get("caseNumber"),
        verdict_date=item.get("verdictDate"),
        title=item.get("title") or "",
        keywords=item.get("keywords") or [],
        text=text,
        text_source=text_source,
        provenance=Provenance(
            publisher=court_name or "Íslenskir dómstólar",
            source_url=f"https://island.is/domar/{ruling_id}",
            retrieved_at=utc_now(),
            authority_class=authority_class,
            authority_label=authority_label,
        ),
    )
