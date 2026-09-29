"""Eurostat discovery: catalogue search and dataset structure (dimensions / valid codes).

Eurostat's data endpoint needs exact dataset codes and dimension codes, and silently ignores
unknown filter *values* (an invalid geo code means "no filter" -> 413 extraction too big). So
callers need a way to find datasets and to see the valid codes before querying.

- Catalogue: the table of contents (tab-separated, indentation = theme hierarchy), cached 24h.
- Structure: the SDMX 2.1 dataflow with `references=descendants&detail=referencepartial`,
  which returns codelists restricted to the codes actually used by that dataset. Cached per
  dataset for the process lifetime (structure changes rarely).
"""

from __future__ import annotations

import re
import time

import httpx
from lxml import etree
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from .hagstofa_catalog import fold

EUROSTAT_API = "https://ec.europa.eu/eurostat/api/dissemination/"
USER_AGENT = "IcelandTrustedContextMCPPoC/0.1 (+public research proof of concept)"
TOC_TTL_SECONDS = 24 * 3600

_NS = {
    "s": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "c": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
}
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(60.0, connect=10.0)


# ---------------------------------------------------------------------------
# Catalogue search
# ---------------------------------------------------------------------------


class EurostatCatalogEntry(BaseModel):
    code: str
    title: str
    theme: str
    last_data_update: str | None = None
    data_start: str | None = None
    data_end: str | None = None
    observation_count: int | None = None


_toc: list[EurostatCatalogEntry] = []
_toc_loaded_at = 0.0


def _parse_toc(text: str) -> list[EurostatCatalogEntry]:
    entries: list[EurostatCatalogEntry] = []
    stack: list[tuple[int, str]] = []  # (indent, folder title)
    for line in text.splitlines()[1:]:
        cells = [c.strip('"') for c in line.split("\t")]
        if len(cells) < 3:
            continue
        raw_title, code, kind = cells[0], cells[1].strip(), cells[2]
        indent = len(raw_title) - len(raw_title.lstrip(" "))
        title = raw_title.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if kind == "folder":
            stack.append((indent, title))
            continue
        if kind != "dataset":
            continue

        def cell(i: int) -> str | None:
            return cells[i].strip() or None if len(cells) > i else None

        values = cell(7)
        entries.append(
            EurostatCatalogEntry(
                code=code,
                title=title,
                theme=" > ".join(t for _, t in stack[1:]),
                last_data_update=cell(3),
                data_start=cell(5),
                data_end=cell(6),
                observation_count=int(values) if values and values.isdigit() else None,
            )
        )
    return entries


async def load_toc() -> list[EurostatCatalogEntry]:
    global _toc, _toc_loaded_at
    if _toc and time.time() - _toc_loaded_at < TOC_TTL_SECONDS:
        return _toc
    async with httpx.AsyncClient(timeout=_timeout(), headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(f"{EUROSTAT_API}catalogue/toc/txt", params={"lang": "en"})
        response.raise_for_status()
    _toc = _parse_toc(response.content.decode("utf-8-sig"))
    _toc_loaded_at = time.time()
    return _toc


class EurostatSearchHit(BaseModel):
    code: str
    title: str
    theme: str
    data_start: str | None = None
    data_end: str | None = None
    last_data_update: str | None = None
    observation_count: int | None = None


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", fold(text))


_SYNONYMS = {"inflation": ["hicp", "price"], "cpi": ["hicp", "price"], "gdp": ["gross domestic product"]}


def search_toc(entries: list[EurostatCatalogEntry], query: str, limit: int) -> list[EurostatSearchHit]:
    """Rank datasets by how many query terms match (code > title > theme); a dataset matching
    every term always outranks one matching only some, but partial matches are still returned
    so a wordy query with one unlucky term doesn't come back empty."""
    tokens = _tokens(query)
    if not tokens:
        return []
    scored: list[tuple[float, EurostatCatalogEntry]] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.code in seen:  # the ToC lists a dataset under every theme that references it
            continue
        title, code, theme = fold(entry.title), entry.code.lower(), fold(entry.theme)
        score, matched = 0.0, 0
        for token in tokens:
            alternatives = [token] + _SYNONYMS.get(token, [])
            best = 0
            for alt in alternatives:
                if code == alt:
                    best = max(best, 10)
                elif alt in code:
                    best = max(best, 4)
                elif alt in title:
                    best = max(best, 3)
                elif alt in theme:
                    best = max(best, 1)
            if best:
                matched += 1
                score += best
        if matched:
            seen.add(entry.code)
            scored.append((score + (100 if matched == len(tokens) else 0), entry))
    scored.sort(key=lambda p: (-p[0], -(p[1].observation_count or 0)))
    return [EurostatSearchHit(**e.model_dump()) for _, e in scored[:limit]]


# ---------------------------------------------------------------------------
# Dataset structure
# ---------------------------------------------------------------------------


class EurostatDimension(BaseModel):
    id: str
    position: int
    is_time: bool = False
    codes: dict[str, str]  # code -> English label (empty for the time dimension)


class EurostatStructure(BaseModel):
    dataset: str
    title: str
    dimensions: list[EurostatDimension]


_structures: dict[str, EurostatStructure] = {}


def _name(element: etree._Element) -> str:
    names = element.findall("c:Name", _NS)
    for n in names:
        if n.get(_XML_LANG) == "en" and n.text:
            return n.text
    return names[0].text if names and names[0].text else ""


def parse_structure(dataset: str, xml_bytes: bytes) -> EurostatStructure:
    root = etree.fromstring(xml_bytes)
    codelists = {
        cl.get("id"): {c.get("id"): _name(c) for c in cl.findall("s:Code", _NS)}
        for cl in root.iterfind(".//s:Codelist", _NS)
    }
    dataflow = root.find(".//s:Dataflow", _NS)
    title = _name(dataflow) if dataflow is not None else dataset
    dsd = root.find(".//s:DataStructure", _NS)
    if dsd is None:
        raise ValueError("no DataStructure in response")
    dimensions: list[EurostatDimension] = []
    for node in dsd.iterfind(".//s:DimensionList/*", _NS):
        ref = node.find(".//s:Enumeration/Ref", _NS)
        is_time = etree.QName(node).localname == "TimeDimension"
        dimensions.append(
            EurostatDimension(
                id=node.get("id", ""),
                position=int(node.get("position", len(dimensions) + 1)),
                is_time=is_time,
                codes=codelists.get(ref.get("id"), {}) if ref is not None else {},
            )
        )
    return EurostatStructure(dataset=dataset, title=title, dimensions=dimensions)


async def load_structure(dataset: str) -> EurostatStructure:
    key = dataset.strip().lower()
    if key in _structures:
        return _structures[key]
    if not re.fullmatch(r"[a-z0-9_]+", key):
        raise ToolError(f"'{dataset}' is not a valid Eurostat dataset code (letters, digits and underscores only).")
    url = f"{EUROSTAT_API}sdmx/2.1/dataflow/ESTAT/{key}/latest"
    async with httpx.AsyncClient(timeout=_timeout(), headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url, params={"references": "descendants", "detail": "referencepartial"})
    if response.status_code in (400, 404):
        raise ToolError(
            f"No Eurostat dataset '{dataset}'. Find the code with search_eurostat_datasets — dataset codes "
            "are short identifiers like 'prc_hicp_midx' and can't be guessed from a title."
        )
    response.raise_for_status()
    structure = parse_structure(key, response.content)
    _structures[key] = structure
    return structure


def dimension_by_id(structure: EurostatStructure, dimension: str) -> EurostatDimension:
    for d in structure.dimensions:
        if d.id.lower() == dimension.lower():
            return d
    raise ToolError(
        f"Dataset '{structure.dataset}' has no dimension '{dimension}'. Its dimensions are: "
        + ", ".join(d.id for d in structure.dimensions)
    )


def validate_filters(structure: EurostatStructure, filters: dict[str, str]) -> None:
    """Reject unknown dimensions/values up front: Eurostat silently ignores an unknown *value*
    (treating it as 'no filter'), which surfaces as a misleading 'extraction too big' error."""
    for dim_id, value in filters.items():
        dimension = dimension_by_id(structure, dim_id)
        if dimension.is_time or not dimension.codes:
            continue
        unknown = [v for v in str(value).split("+") if v and v not in dimension.codes]
        if unknown:
            sample = ", ".join(f"{c} ({l[:30]})" for c, l in list(dimension.codes.items())[:6])
            raise ToolError(
                f"Value(s) {unknown} are not valid for dimension '{dimension.id}' of '{structure.dataset}'. "
                f"Use get_eurostat_dimension_values to list valid codes (e.g. {sample})."
            )


def eurostat_error_message(response: httpx.Response) -> str | None:
    """Eurostat returns errors as {"error": [{"status", "label"}]} — pull out the label."""
    try:
        return response.json()["error"][0]["label"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
