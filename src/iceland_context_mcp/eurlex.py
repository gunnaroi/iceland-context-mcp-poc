"""EU law discovery over CELLAR (the Publications Office's semantic repository).

Complements `sources.fetch_eur_lex_act` (text of one act by CELEX) with the questions that come before
and after it: what is the CELEX of "Regulation (EU) 2016/679"? Which acts amended or repealed it, is a
newer consolidated text in force, which legislation or CJEU judgments mention a subject? All of it is
plain SPARQL against the public CDM ontology endpoint; free-text search uses Virtuoso's `bif:contains`
full-text operator, because a `CONTAINS(LCASE(title))` scan over the whole repository times out.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

CELLAR_SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
USER_AGENT = "IcelandTrustedContextMCPPoC/0.1 (+public research proof of concept)"
ENG = "<http://publications.europa.eu/resource/authority/language/ENG>"
PREFIXES = (
    "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
    "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def sparql(query: str) -> list[dict]:
    timeout = httpx.Timeout(45.0, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(
                CELLAR_SPARQL_ENDPOINT,
                params={"query": PREFIXES + query, "format": "application/sparql-results+json"},
            )
    except httpx.TimeoutException:
        raise ToolError("The EU Publications Office (CELLAR) query timed out. Narrow the search and retry.") from None
    if response.status_code != 200:
        raise ToolError(f"CELLAR SPARQL endpoint returned HTTP {response.status_code}; retry shortly.")
    return response.json()["results"]["bindings"]


def _val(row: dict, key: str) -> str | None:
    return row[key]["value"] if key in row else None


# ---------------------------------------------------------------------------
# CELEX helpers
# ---------------------------------------------------------------------------

TYPE_LETTERS = {"regulation": "R", "directive": "L", "decision": "D", "framework decision": "F"}
CELEX_TYPE_LABEL = {
    "R": "Regulation", "L": "Directive", "D": "Decision", "F": "Framework decision",
    "H": "Recommendation", "M": "Merger decision", "A": "Opinion", "B": "Budget",
}  # fmt: skip
CASE_DOC_KINDS = {
    "C": ("CJ", "CC", "CO", "CA", "CV", "CD", "CN", "CS", "CX", "CP"),
    "T": ("TJ", "TO", "TC", "TN", "TA", "TB"),
    "F": ("FJ", "FO", "FN", "FA"),
}
CASE_TYPE_LABEL = {
    "CN": "Case notice (pending case)", "CJ": "Judgment of the Court of Justice", "CC": "Opinion of the Advocate General",
    "CO": "Order of the Court of Justice", "TJ": "Judgment of the General Court",
    "TO": "Order of the General Court", "CA": "Court of Justice ruling (art. 43)", "CV": "Opinion of the Court",
    "CD": "Court of Justice decision", "TC": "General Court opinion", "CS": "Court of Justice, other",
}  # fmt: skip


def _celex_type(celex: str) -> str | None:
    if re.fullmatch(r"3\d{4}[A-Z]\d{4}", celex):
        return CELEX_TYPE_LABEL.get(celex[5])
    if re.fullmatch(r"6\d{4}[A-Z]{2}\d{4}", celex):
        return CASE_TYPE_LABEL.get(celex[5:7], "Case-law document")
    if celex.startswith("0"):
        return "Consolidated text"
    return None


def _norm_year(raw: str) -> int | None:
    year = int(raw)
    if len(raw) == 2:
        year += 1900
    return year if 1950 <= year <= 2100 else None


_CITATION_RE = re.compile(
    r"(?P<kind>council framework decision|framework decision|regulation|directive|decision)\s*"
    r"(?:\((?:EU|EC|EEC|EURATOM|CFSP|Euratom|ECSC)\)\s*)?(?:No\.?\s*)?(?P<a>\d{1,4})\s*/\s*(?P<b>\d{1,4})",
    re.IGNORECASE,
)
_CELEX_RE = re.compile(r"^[0-9CE][0-9A-Z()_-]{7,24}$")
_ELI_RE = re.compile(r"data\.europa\.eu/eli/(reg|dir|dec)(?:_impl|_del)?/(\d{4})/(\d+)", re.IGNORECASE)


def celex_candidates(citation: str) -> list[str]:
    """CELEX numbers a citation could denote. The year/number order is ambiguous ('Regulation (EC) No
    1049/2001' is number/year, 'Regulation (EU) 2016/679' is year/number), so both are generated and the
    caller keeps whichever exists in CELLAR."""
    text = re.sub(r"[‐-―]", "-", citation).strip()
    compact = re.sub(r"\s+", "", text).upper()
    if _CELEX_RE.fullmatch(compact) and re.search(r"\d{4}", compact):
        return [compact]
    eli = _ELI_RE.search(text)
    if eli:
        letter = {"reg": "R", "dir": "L", "dec": "D"}[eli.group(1).lower()]
        return [f"3{eli.group(2)}{letter}{int(eli.group(3)):04d}"]
    match = _CITATION_RE.search(text)
    if not match:
        return []
    kind = match.group("kind").lower().replace("council ", "")
    letter = TYPE_LETTERS[kind]
    a, b = match.group("a"), match.group("b")
    candidates: list[str] = []
    for year_raw, number_raw in ((a, b), (b, a)):
        year = _norm_year(year_raw)
        number = int(number_raw)
        if year and 0 < number <= 9999:
            candidate = f"3{year}{letter}{number:04d}"
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def parse_case_number(text: str) -> tuple[str, int, int] | None:
    """'C-131/12' -> ('C', 2012, 131). Old-style 'Case 26/62' (no prefix) is Court of Justice."""
    text = re.sub(r"[‐-―]", "-", text)
    match = re.search(r"\b([CTF])\s*-\s*(\d{1,4})\s*/\s*(\d{2,4})\b", text, re.IGNORECASE)
    if match:
        court, number, year_raw = match.group(1).upper(), int(match.group(2)), match.group(3)
    else:
        match = re.fullmatch(r"\s*(\d{1,4})\s*/\s*(\d{2})\s*", text)
        if not match:
            return None
        court, number, year_raw = "C", int(match.group(1)), match.group(2)
    if len(year_raw) == 2:
        year = 1900 + int(year_raw) if int(year_raw) >= 53 else 2000 + int(year_raw)
    else:
        year = int(year_raw)
    return court, year, number


def _fulltext_expression(keyword: str) -> str:
    words = re.findall(r"[\w][\w'-]*", keyword, re.UNICODE)
    words = [w.replace("'", "") for w in words if w.replace("'", "")]
    if not words:
        raise ToolError("Keyword must contain at least one letter or digit.")
    return " AND ".join(f"'{w}'" for w in words[:6])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class EuActRef(BaseModel):
    celex: str
    title: str | None = None
    date: str | None = None
    act_type: str | None = None
    in_force: bool | None = None
    ecli: str | None = None


class EuLookupResult(BaseModel):
    query: str
    matches: list[EuActRef]
    interpreted_as: list[str]
    source_url: str = CELLAR_SPARQL_ENDPOINT
    retrieved_at: str
    note: str = (
        "Pass a match's `celex` to get_eur_lex_act (text), get_eu_act_relations (amendments, repeals, "
        "consolidated versions, legal basis) or trace_eea_public_context (Icelandic/EEA side). An EU act being "
        "in force says nothing about Icelandic applicability by itself."
    )


class EuSearchResult(BaseModel):
    query: str
    hits: list[EuActRef]
    returned: int
    source_url: str = CELLAR_SPARQL_ENDPOINT
    retrieved_at: str
    note: str = (
        "Newest first. Titles are English; `date` is the act's/document's own date. For acts, in_force is the "
        "EU-level status only."
    )


class EuRelationGroup(BaseModel):
    relation: str
    description: str
    total: int
    items: list[EuActRef]


class EuRelationsResult(BaseModel):
    celex: str
    title: str | None
    in_force: bool | None
    relations: list[EuRelationGroup]
    source_url: str = CELLAR_SPARQL_ENDPOINT
    retrieved_at: str
    note: str = (
        "Consolidated versions are CELEX numbers starting with 0 (e.g. 02016R0679-20160504): pass them to "
        "get_eur_lex_act for the text with all amendments applied. Groups with total > len(items) are truncated; "
        "call again with relation=<name> and a larger limit."
    )


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

_ACT_SELECT = """SELECT ?c ?title ?date ?inForce WHERE {{
  VALUES ?c {{ {values} }}
  ?w cdm:resource_legal_id_celex ?c .
  OPTIONAL {{ ?w cdm:work_date_document ?date }}
  OPTIONAL {{ ?w cdm:resource_legal_in-force ?inForce }}
  OPTIONAL {{ ?e cdm:expression_belongs_to_work ?w ; cdm:expression_uses_language {eng} ; cdm:expression_title ?title }}
}}"""


def _dedupe(refs: list[EuActRef]) -> list[EuActRef]:
    """CELLAR returns one row per title/ECLI variant of the same work; keep the first per CELEX."""
    seen: dict[str, EuActRef] = {}
    for ref in refs:
        seen.setdefault(ref.celex, ref)
    return list(seen.values())


def _act_ref(row: dict) -> EuActRef:
    celex = _val(row, "c") or ""
    in_force = _val(row, "inForce")
    return EuActRef(
        celex=celex,
        title=_val(row, "title"),
        date=_val(row, "date"),
        act_type=_celex_type(celex),
        in_force=(in_force in ("1", "true")) if in_force is not None else None,
        ecli=_val(row, "ecli"),
    )


async def lookup_eu_act(citation: str) -> EuLookupResult:
    candidates = celex_candidates(citation)
    if not candidates:
        case = parse_case_number(citation)
        hint = " For a court case number like 'C-131/12' use search_eu_case_law." if case or "/" in citation else ""
        raise ToolError(
            f"Could not read '{citation}' as an EU act citation. Accepted: a CELEX number (32016R0679), an ELI "
            "URI, or a citation such as 'Regulation (EU) 2016/679', 'Directive 2000/31/EC' or 'Regulation (EC) "
            f"No 1049/2001'.{hint} To find acts by subject use search_eu_legislation."
        )
    values = " ".join(f'"{c}"^^xsd:string' for c in candidates)
    rows = await sparql(_ACT_SELECT.format(values=values, eng=ENG))
    seen: dict[str, EuActRef] = {}
    for row in rows:
        ref = _act_ref(row)
        seen.setdefault(ref.celex, ref)
    if not seen:
        raise ToolError(
            f"No EU act found for '{citation}' (tried CELEX {', '.join(candidates)}). Check the number/year, "
            "or use search_eu_legislation to search by subject."
        )
    return EuLookupResult(
        query=citation, matches=list(seen.values()), interpreted_as=candidates, retrieved_at=_utc_now()
    )


# ---------------------------------------------------------------------------
# Search: legislation and case law
# ---------------------------------------------------------------------------


async def search_eu_legislation(
    keyword: str,
    act_type: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    in_force_only: bool = False,
    limit: int = 15,
) -> EuSearchResult:
    limit = max(1, min(limit, 50))
    filters = ['FILTER(STRSTARTS(STR(?c),"3"))', 'FILTER(!CONTAINS(STR(?c),"("))']
    if act_type:
        letter = TYPE_LETTERS.get(act_type.lower())
        if not letter:
            raise ToolError("act_type must be one of: regulation, directive, decision.")
        filters.append(f'FILTER(REGEX(STR(?c),"^3[0-9]{{4}}{letter}"))')
    if year_from:
        filters.append(f'FILTER(?date >= "{int(year_from)}-01-01"^^xsd:date)')
    if year_to:
        filters.append(f'FILTER(?date <= "{int(year_to)}-12-31"^^xsd:date)')
    if in_force_only:
        filters.append('FILTER(STR(?inForce) IN ("1","true"))')
    query = f"""SELECT DISTINCT ?c ?title ?date ?inForce WHERE {{
  ?e cdm:expression_title ?title . ?title bif:contains "{_fulltext_expression(keyword)}" .
  ?e cdm:expression_uses_language {ENG} ; cdm:expression_belongs_to_work ?w .
  ?w cdm:resource_legal_id_celex ?c .
  OPTIONAL {{ ?w cdm:work_date_document ?date }}
  OPTIONAL {{ ?w cdm:resource_legal_in-force ?inForce }}
  {" ".join(filters)}
}} ORDER BY DESC(?date) LIMIT {limit}"""
    rows = await sparql(query)
    hits = _dedupe([_act_ref(r) for r in rows])
    return EuSearchResult(query=keyword, hits=hits, returned=len(hits), retrieved_at=_utc_now())


async def search_eu_case_law(
    case_number: str | None = None,
    keyword: str | None = None,
    court: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 15,
) -> EuSearchResult:
    if not case_number and not keyword:
        raise ToolError("Give a case_number (e.g. 'C-131/12', 'T-22/20') and/or a keyword (party or subject words).")
    limit = max(1, min(limit, 50))
    filters = ['FILTER(STRSTARTS(STR(?c),"6"))', 'FILTER(!CONTAINS(STR(?c),"_"))']
    joins = ""
    if keyword:
        joins = f"""?title bif:contains "{_fulltext_expression(keyword)}" ."""
    if case_number:
        parsed = parse_case_number(case_number)
        if not parsed:
            raise ToolError(f"Could not read '{case_number}' as a case number; expected e.g. 'C-131/12' or 'T-22/20'.")
        letter, year, number = parsed
        # Exact CELEX candidates (VALUES) — a REGEX over every CELEX in the repository times out.
        kinds = CASE_DOC_KINDS[letter]
        celexes = " ".join(f'"6{year}{kind}{number:04d}"^^xsd:string' for kind in kinds)
        joins += f"VALUES ?c {{ {celexes} }}\n  "
    if court:
        letter = {"cjeu": "C", "court of justice": "C", "gc": "T", "general court": "T"}.get(court.lower())
        if not letter:
            raise ToolError("court must be 'CJEU' (Court of Justice) or 'GC' (General Court).")
        filters.append(f'FILTER(REGEX(STR(?c),"^6[0-9]{{4}}{letter}"))')
    if year_from:
        filters.append(f'FILTER(?date >= "{int(year_from)}-01-01"^^xsd:date)')
    if year_to:
        filters.append(f'FILTER(?date <= "{int(year_to)}-12-31"^^xsd:date)')
    query = f"""SELECT DISTINCT ?c ?title ?date ?ecli WHERE {{
  ?e cdm:expression_title ?title . {joins}
  ?e cdm:expression_uses_language {ENG} ; cdm:expression_belongs_to_work ?w .
  ?w cdm:resource_legal_id_celex ?c .
  OPTIONAL {{ ?w cdm:work_date_document ?date }}
  OPTIONAL {{ ?w cdm:case-law_ecli ?ecli }}
  {" ".join(filters)}
}} ORDER BY DESC(?date) LIMIT {limit}"""
    rows = await sparql(query)
    hits = _dedupe([_act_ref(r) for r in rows])
    label = " / ".join(x for x in (case_number, keyword) if x)
    return EuSearchResult(
        query=label,
        hits=hits,
        returned=len(hits),
        retrieved_at=_utc_now(),
        note=(
            "Newest first. One case can have several documents (judgment, Advocate General opinion, orders) — "
            "`act_type` tells them apart; the judgment is usually the one to read. Pass a `celex` to "
            "get_eur_lex_act for the text."
        ),
    )


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------

# name -> (direction relative to the queried act, CDM predicate, description)
RELATIONS: dict[str, tuple[str, str, str]] = {
    "amended_by": ("in", "resource_legal_amends_resource_legal", "Acts that amend this act"),
    "amends": ("out", "resource_legal_amends_resource_legal", "Acts this act amends"),
    "repealed_by": ("in", "resource_legal_repeals_resource_legal", "Acts that repeal this act"),
    "repeals": ("out", "resource_legal_repeals_resource_legal", "Acts this act repeals"),
    "legal_basis": ("out", "resource_legal_based_on_resource_legal", "Acts this act is legally based on"),
    "consolidated_versions": (
        "in",
        "act_consolidated_consolidates_resource_legal",
        "Consolidated texts (this act with amendments applied), newest first",
    ),
    "corrigenda": ("in", "resource_legal_corrects_resource_legal", "Corrections published for this act"),
    "acts_based_on_this": ("in", "resource_legal_based_on_resource_legal", "Acts adopted on the basis of this act"),
    "case_law_interpreting": ("in", "case-law_interpretes_resource_legal", "CJEU/General Court case-law interpreting this act"),
    "national_implementing_measures": (
        "in",
        "measure_national_implementing_implements_resource_legal",
        "Member-state measures implementing this (directive) act — count only by default",
    ),
    "cited_by": ("in", "work_cites_work", "Documents citing this act — count only by default"),
    "cites": ("out", "work_cites_work", "Documents this act cites — count only by default"),
}
_DEFAULT_ITEM_RELATIONS = (
    "amended_by", "repealed_by", "repeals", "amends", "legal_basis", "consolidated_versions", "corrigenda",
)  # fmt: skip
_ITEMS_ON_SUMMARY = 5


async def _relation_items(celex: str, relation: str, limit: int) -> list[EuActRef]:
    direction, predicate, _ = RELATIONS[relation]
    link = f"?w cdm:{predicate} ?t" if direction == "out" else f"?t cdm:{predicate} ?w"
    query = f"""SELECT DISTINCT ?c ?title ?date ?inForce WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^xsd:string .
  {link} .
  ?t cdm:resource_legal_id_celex ?c .
  OPTIONAL {{ ?t cdm:work_date_document ?date }}
  OPTIONAL {{ ?t cdm:resource_legal_in-force ?inForce }}
  OPTIONAL {{ ?e cdm:expression_belongs_to_work ?t ; cdm:expression_uses_language {ENG} ; cdm:expression_title ?title }}
}} ORDER BY DESC(?date) LIMIT {limit}"""
    return _dedupe([_act_ref(r) for r in await sparql(query)])


async def get_eu_act_relations(celex: str, relation: str | None = None, limit: int = 15) -> EuRelationsResult:
    celex = re.sub(r"\s+", "", celex).upper()
    if relation and relation not in RELATIONS:
        raise ToolError(f"Unknown relation '{relation}'. Choose one of: {', '.join(RELATIONS)}.")
    limit = max(1, min(limit, 100))
    base = await sparql(_ACT_SELECT.format(values=f'"{celex}"^^xsd:string', eng=ENG))
    if not base:
        raise ToolError(
            f"No EU act with CELEX '{celex}'. Use lookup_eu_act to resolve a citation like 'Regulation (EU) 2016/679'."
        )
    head = _act_ref(base[0])

    async def counts(direction: str) -> dict[str, int]:
        pattern = "?w ?p ?o" if direction == "out" else "?o ?p ?w"
        rows = await sparql(
            f'SELECT ?p (COUNT(DISTINCT ?o) AS ?n) WHERE {{ ?w cdm:resource_legal_id_celex "{celex}"^^xsd:string . '
            f"{pattern} }} GROUP BY ?p"
        )
        return {r["p"]["value"].split("#")[-1]: int(r["n"]["value"]) for r in rows}

    out_counts, in_counts = await asyncio.gather(counts("out"), counts("in"))
    totals = {
        name: (out_counts if direction == "out" else in_counts).get(predicate, 0)
        for name, (direction, predicate, _) in RELATIONS.items()
    }
    if relation:
        chosen = {relation: limit}
    else:
        chosen = {name: _ITEMS_ON_SUMMARY for name in _DEFAULT_ITEM_RELATIONS if totals[name]}
    fetched = await asyncio.gather(*(_relation_items(celex, name, n) for name, n in chosen.items()))
    items = dict(zip(chosen, fetched))
    groups = [
        EuRelationGroup(
            relation=name, description=RELATIONS[name][2], total=totals[name], items=items.get(name, [])
        )
        for name in RELATIONS
        if totals[name] or name == relation
    ]
    return EuRelationsResult(
        celex=head.celex, title=head.title, in_force=head.in_force, relations=groups, retrieved_at=_utc_now()
    )
