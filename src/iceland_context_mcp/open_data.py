"""Live retrieval tools for public Icelandic (and EU-counterpart) data sources
beyond this PoC's core legal/EEA scope — see README "Reference resources
beyond this PoC's own scope". Unlike sources.py, these carry no
authority-class/provenance discipline: this data isn't legal in nature, so
"authoritative vs. discovery" doesn't apply the same way. Each source here
has a genuine public API/WFS/bulk-download endpoint — nothing here scrapes
rendered HTML or reverse-engineers a Power BI/Tableau dashboard.

Sources were selected from jokull/icelandic-data's SKILL.md docs (vendored
in ./skills/) — see THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import base64
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from .eurostat import (
    EUROSTAT_API,
    EurostatSearchHit,
    dimension_by_id,
    load_structure,
    load_toc,
    search_toc,
)

OPEN_DATA_REGISTRY_PATH = Path(__file__).with_name("open_data_registry.json")
USER_AGENT = "IcelandTrustedContextMCPPoC/0.1 (+public research proof of concept)"
MAX_ROWS = 500
MAX_FEATURES = 500


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OpenDataSourceRecord(BaseModel):
    key: str
    name: str
    publisher: str
    base_url: str
    notes: str
    wfs_version: str = "1.0.0"


def load_open_data_registry() -> dict[str, dict]:
    return json.loads(OPEN_DATA_REGISTRY_PATH.read_text(encoding="utf-8"))


def open_data_registry_records() -> list[OpenDataSourceRecord]:
    return [OpenDataSourceRecord(key=k, **v) for k, v in load_open_data_registry().items()]


def open_data_registry_record(key: str) -> OpenDataSourceRecord:
    data = load_open_data_registry()
    if key not in data:
        raise ValueError(f"Unknown open-data source key: {key}. Known keys: {', '.join(sorted(data))}")
    return OpenDataSourceRecord(key=key, **data[key])


async def _get_json(url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    timeout = httpx.Timeout(30.0, connect=10.0)
    merged_headers = {"User-Agent": USER_AGENT, "Accept-Language": "is,en;q=0.8"}
    if headers:
        merged_headers.update(headers)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=merged_headers) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


async def _post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    timeout = httpx.Timeout(30.0, connect=10.0)
    merged_headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    if headers:
        merged_headers.update(headers)
    async with httpx.AsyncClient(timeout=timeout, headers=merged_headers) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


# ---------------------------------------------------------------------------
# Generic WFS geodata (umferd, fiskistofa, ust-gis, lmi)
# ---------------------------------------------------------------------------


class GeoDataResult(BaseModel):
    source_key: str
    layer: str
    feature_count: int
    truncated: bool
    features: list[dict] = Field(default_factory=list)
    source_url: str
    retrieved_at: str
    note: str = (
        "Raw GeoServer WFS features (GeoJSON), truncated to a sample. This is current-state spatial data, "
        "not an event history — snapshot it yourself if you need historical comparison."
    )


async def get_geodata(
    source_key: str, layer: str, cql_filter: str | None = None, srs: str = "EPSG:4326", limit: int = 50
) -> GeoDataResult:
    source = open_data_registry_record(source_key)
    limit = max(1, min(limit, MAX_FEATURES))
    base_url = source.base_url
    if "{workspace}" in base_url:
        if ":" not in layer:
            raise ValueError(f"For source {source_key!r}, layer must be 'WORKSPACE:LayerName' (e.g. 'ERM:Landmask').")
        workspace = layer.split(":", 1)[0]
        base_url = base_url.replace("{workspace}", workspace)
    params = {
        "service": "WFS",
        "version": source.wfs_version,
        "request": "GetFeature",
        "typeName": layer,
        "outputFormat": "application/json",
        "srsName": srs,
        "maxFeatures": limit,
        "count": limit,
    }
    if cql_filter:
        params["cql_filter"] = cql_filter
    data = await _get_json(base_url, params=params)
    features = data.get("features", [])
    total = data.get("totalFeatures", len(features))
    total_count = total if isinstance(total, int) and total >= 0 else len(features)
    return GeoDataResult(
        source_key=source_key,
        layer=layer,
        feature_count=total_count,
        truncated=len(features) < total_count if isinstance(total_count, int) else False,
        features=features[:limit],
        source_url=base_url,
        retrieved_at=utc_now(),
    )


# ---------------------------------------------------------------------------
# Generic Hagstofa / Statistics Iceland PX-Web (hagstofan, income-distribution, ...)
# ---------------------------------------------------------------------------

HAGSTOFA_BASE = "https://px.hagstofa.is/pxis/api/v1/is/"
HAGSTOFA_BASES = {"is": HAGSTOFA_BASE, "en": "https://px.hagstofa.is/pxen/api/v1/en/"}


def _hagstofa_base(language: str) -> str:
    try:
        return HAGSTOFA_BASES[language.lower()]
    except KeyError:
        raise ToolError("language must be 'is' (Icelandic, default) or 'en' (English, fewer tables).") from None


class StatTableResult(BaseModel):
    table_path: str
    columns: list[str]
    rows: list[list[str]]
    truncated: bool
    total_rows: int
    language: str = "is"
    source_url: str
    retrieved_at: str
    note: str = "Hagstofa Íslands PX-Web table, fetched as CSV and parsed. Values are as published — check the table's own unit/scale conventions (e.g. thousands of ISK) before using them."


def _decode_hagstofa_csv(content: bytes, declared_encoding: str | None) -> str:
    # The declared Content-Type charset is unreliable per-table (some tables send a
    # UTF-8 BOM body but declare charset=Windows-1252 in the header) — a BOM is a
    # hard signal of real encoding, so check for it before trusting the header.
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig")
    return content.decode(declared_encoding or "utf-8-sig")


_hagstofa_meta_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_hagstofa_limits_cache: dict[str, dict] = {}
HAGSTOFA_META_TTL_SECONDS = 3600
# Fallbacks for the PX-Web limits, which Hagstofa publishes at `<base>?config` (checked live: is/en both
# maxCells 100000 / maxValues 5000; a 101,088-cell selection got 403 while 94,770 succeeded).
HAGSTOFA_DEFAULT_LIMITS = {"maxCells": 100_000, "maxValues": 5_000}


async def _hagstofa_limits(language: str) -> dict:
    if language in _hagstofa_limits_cache:
        return _hagstofa_limits_cache[language]
    limits = dict(HAGSTOFA_DEFAULT_LIMITS)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(_hagstofa_base(language), params={"config": ""})
        config = response.json()
        limits.update({k: int(config[k]) for k in ("maxCells", "maxValues") if k in config})
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    _hagstofa_limits_cache[language] = limits
    return limits


class HagstofaValue(BaseModel):
    code: str
    label: str


class HagstofaVariable(BaseModel):
    code: str
    text: str
    is_time: bool
    value_count: int
    values: list[HagstofaValue]
    values_shown_note: str | None = None


class HagstofaTableInfo(BaseModel):
    table_path: str
    language: str
    title: str
    variables: list[HagstofaVariable]
    total_cells: int
    source_url: str
    retrieved_at: str
    note: str = (
        "Pass a variable's `code` as a key in get_hagstofa_table_tool's `filters`, with value `code`s (or exact "
        "labels) as the list; '*' selects every value. Omitted variables default to all values (or are summed "
        "away, for variables the table allows eliminating). For time series use last_n_periods instead of "
        "filtering the time variable."
    )


def _bad_path_error(table_path: str, language: str = "is") -> ToolError:
    english = (
        " Not every table has an English edition — the search results show `title_en` for those that do; "
        "retry with language='is' otherwise."
        if language == "en"
        else ""
    )
    return ToolError(
        f"No PX-Web table at path '{table_path}'. This path must match a real folder/table exactly — there is "
        "no fuzzy matching, and a guessed or partial path always fails this way. Use search_hagstofa_tables "
        "(keyword search) or browse_hagstofa_tables (folder by folder) and pass the returned path unmodified."
        + english
    )


async def _hagstofa_metadata(table_path: str, language: str = "is") -> dict:
    import time

    base = _hagstofa_base(language)
    cache_key = (language, table_path)
    cached = _hagstofa_meta_cache.get(cache_key)
    if cached and time.time() - cached[0] < HAGSTOFA_META_TTL_SECONDS:
        return cached[1]
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(f"{base}{table_path}")
    # PX-Web answers a bare 400/404 (no body) for any path that isn't exactly a table.
    if response.status_code in (400, 404):
        raise _bad_path_error(table_path, language)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError:
        raise _bad_path_error(table_path, language) from None  # a folder path returns a listing, not table metadata
    if not isinstance(data, dict) or "variables" not in data:
        raise ToolError(
            f"'{table_path}' is a folder, not a table. Pass it to browse_hagstofa_tables to list what it contains."
        )
    _hagstofa_meta_cache[cache_key] = (time.time(), data)
    return data


def _variable_values(variable: dict) -> list[HagstofaValue]:
    codes = variable.get("values", [])
    labels = variable.get("valueTexts", codes)
    return [HagstofaValue(code=c, label=l) for c, l in zip(codes, labels)]


def _resolve_filter_values(variable: dict, requested: list[str], table_path: str) -> list[str]:
    """Map requested values to codes. Accepts codes or (case-insensitive) labels; '*' = all."""
    if any(v == "*" for v in requested):
        return ["*"]
    values = _variable_values(variable)
    by_code = {v.code for v in values}
    by_label = {v.label.casefold(): v.code for v in values}
    resolved, unknown = [], []
    for item in requested:
        item = str(item)
        if item in by_code:
            resolved.append(item)
        elif item.casefold() in by_label:
            resolved.append(by_label[item.casefold()])
        else:
            unknown.append(item)
    if unknown:
        sample = ", ".join(f"{v.code} ({v.label[:30]})" for v in values[:6])
        raise ToolError(
            f"Value(s) {unknown} not found in variable '{variable['code']}' of '{table_path}'. Call "
            f"get_hagstofa_table_info with variable='{variable['code']}' to list valid values (e.g. {sample})."
        )
    return resolved


async def get_hagstofa_table_info(
    table_path: str, variable: str | None = None, query: str | None = None, limit: int = 50, language: str = "is"
) -> HagstofaTableInfo:
    table_path = table_path.strip("/")
    language = language.lower()
    meta = await _hagstofa_metadata(table_path, language)
    variables_out: list[HagstofaVariable] = []
    total_cells = 1
    for var in meta["variables"]:
        values = _variable_values(var)
        total_cells *= max(len(values), 1)
        if variable is not None and var["code"].casefold() != variable.casefold():
            continue
        is_time = bool(var.get("time"))
        note = None
        if variable is not None:
            if query:
                needle = query.casefold()
                values = [v for v in values if needle in v.code.casefold() or needle in v.label.casefold()]
            shown = values[:limit]
            if len(values) > len(shown):
                note = f"Showing {len(shown)} of {len(values)} matching values; narrow with `query` or raise `limit`."
        elif is_time and len(values) > 8:
            shown = values[:2] + values[-4:]
            note = f"Time variable: first 2 and last 4 of {len(values)} periods shown."
        elif len(values) > 8:
            shown = values[:8]
            note = f"First 8 of {len(values)} values shown; pass variable='{var['code']}' to list them all."
        else:
            shown = values
        variables_out.append(
            HagstofaVariable(
                code=var["code"],
                text=var.get("text", var["code"]),
                is_time=is_time,
                value_count=len(_variable_values(var)),
                values=shown,
                values_shown_note=note,
            )
        )
    if variable is not None and not variables_out:
        raise ToolError(
            f"Table '{table_path}' has no variable '{variable}'. Its variables are: "
            + ", ".join(v["code"] for v in meta["variables"])
        )
    return HagstofaTableInfo(
        table_path=table_path,
        language=language,
        title=meta.get("title", ""),
        variables=variables_out,
        total_cells=total_cells,
        source_url=f"{_hagstofa_base(language)}{table_path}",
        retrieved_at=utc_now(),
    )


async def get_hagstofa_table(
    table_path: str,
    filters: dict[str, list[str]] | None = None,
    last_n_periods: int | None = None,
    language: str = "is",
) -> StatTableResult:
    table_path = table_path.strip("/")
    language = language.lower()
    url = f"{_hagstofa_base(language)}{table_path}"
    meta = await _hagstofa_metadata(table_path, language)
    limits = await _hagstofa_limits(language)
    variables = {v["code"].casefold(): v for v in meta["variables"]}
    time_var = next((v for v in meta["variables"] if v.get("time")), None)

    query = []
    selected_counts: dict[str, int] = {}
    for code, requested in (filters or {}).items():
        var = variables.get(code.casefold())
        if var is None:
            raise ToolError(
                f"Table '{table_path}' has no variable '{code}'. Its variables are: "
                + ", ".join(v["code"] for v in meta["variables"])
                + ". Call get_hagstofa_table_info for their values."
            )
        if isinstance(requested, str):
            requested = [requested]
        if last_n_periods is not None and var is time_var:
            raise ToolError("Use either last_n_periods or a filter on the time variable, not both.")
        values = _resolve_filter_values(var, list(requested), table_path)
        if values == ["*"]:
            query.append({"code": var["code"], "selection": {"filter": "all", "values": ["*"]}})
            selected_counts[var["code"]] = len(var["values"])
        else:
            query.append({"code": var["code"], "selection": {"filter": "item", "values": values}})
            selected_counts[var["code"]] = len(values)
    if last_n_periods is not None:
        if time_var is None:
            raise ToolError(f"Table '{table_path}' has no time variable, so last_n_periods does not apply.")
        if last_n_periods < 1:
            raise ToolError("last_n_periods must be at least 1.")
        query.append({"code": time_var["code"], "selection": {"filter": "top", "values": [str(last_n_periods)]}})
        selected_counts[time_var["code"]] = min(last_n_periods, len(time_var["values"]))

    estimated_cells = 1
    selected_values = 0
    for var in meta["variables"]:
        count = selected_counts.get(var["code"], len(var["values"]))
        estimated_cells *= count
        selected_values += count
    if selected_values > limits["maxValues"]:
        raise ToolError(
            f"This selection names {selected_values:,} values across all variables, over Hagstofa's "
            f"{limits['maxValues']:,}-value limit (omitted variables count as all values). Filter the "
            "variables with the most values (see get_hagstofa_table_info) or set last_n_periods."
        )
    if estimated_cells > limits["maxCells"]:
        biggest = sorted(
            (v for v in meta["variables"] if v["code"] not in selected_counts),
            key=lambda v: -len(v["values"]),
        )[:3]
        raise ToolError(
            f"This selection is about {estimated_cells:,} cells, over Hagstofa's {limits['maxCells']:,}-cell limit. "
            "Narrow it with `filters` (most values live in: "
            + ", ".join(f"{v['code']} [{len(v['values'])}]" for v in biggest)
            + (f") or set last_n_periods for the time variable '{time_var['code']}'." if time_var else ").")
        )

    payload = {"query": query, "response": {"format": "csv"}}
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.post(url, json=payload)
        if response.status_code == 403:
            raise ToolError(
                "Hagstofa rejected the query as too large (403). Narrow it with `filters` or set last_n_periods."
            )
        if response.status_code == 400:
            raise ToolError(
                f"Hagstofa rejected the query for '{table_path}' (400). Check filter values with "
                "get_hagstofa_table_info."
            )
        response.raise_for_status()
        text = _decode_hagstofa_csv(response.content, response.encoding)
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if row]
    header, body = (rows[0], rows[1:]) if rows else ([], [])
    truncated = len(body) > MAX_ROWS
    return StatTableResult(
        table_path=table_path,
        columns=header,
        rows=body[:MAX_ROWS],
        truncated=truncated,
        total_rows=len(body),
        language=language,
        source_url=url,
        retrieved_at=utc_now(),
    )


class HagstofaSearchHit(BaseModel):
    path: str
    title: str
    title_en: str | None = None
    folder: str
    updated: str | None = None


class HagstofaSearchResult(BaseModel):
    query: str
    hits: list[HagstofaSearchHit]
    tables_indexed: int
    catalogue_crawled_at: str
    catalogue_refreshing: bool
    note: str = (
        "Locally cached index of Hagstofa's PX-Web catalogue (PX-Web has no search). Pass a hit's `path` unmodified "
        "to get_hagstofa_table_info, then get_hagstofa_table_tool. Hits with `title_en` also exist in English "
        "(pass language='en' to the table tools for English variable names and labels). No hits: try fewer words."
    )


async def search_hagstofa_catalog(query: str, limit: int = 15) -> HagstofaSearchResult:
    from . import hagstofa_catalog as catalog

    limit = max(1, min(limit, 50))
    snapshot = catalog.load_snapshot()
    if snapshot is None:
        # No seed shipped and no cache yet: crawl in the foreground (a few minutes) is too slow for a
        # tool call, so start it in the background and tell the caller to fall back to browsing.
        catalog.ensure_fresh()
        raise ToolError(
            "The Hagstofa catalogue index is being built (first run takes a few minutes). Use "
            "browse_hagstofa_tables meanwhile, or retry shortly."
        )
    catalog.ensure_fresh()  # kicks off a background re-crawl when the snapshot is stale
    hits = catalog.search_catalog(snapshot, query, limit)
    return HagstofaSearchResult(
        query=query,
        hits=[
            HagstofaSearchHit(path=h.path, title=h.title, title_en=h.title_en, folder=h.folder, updated=h.updated)
            for h in hits
        ],
        tables_indexed=len(snapshot.tables),
        catalogue_crawled_at=snapshot.crawled_at,
        catalogue_refreshing=catalog.refreshing(),
    )


class HagstofaBrowseEntry(BaseModel):
    id: str
    entry_type: str
    title: str
    updated: str | None = None
    full_path: str


class HagstofaBrowseResult(BaseModel):
    path: str
    entries: list[HagstofaBrowseEntry]
    source_url: str
    retrieved_at: str
    note: str = (
        "entry_type='folder' can be browsed further by passing its full_path back into this tool. "
        "entry_type='table' entries' full_path can be passed directly to get_hagstofa_table."
    )


async def browse_hagstofa(path: str = "", language: str = "is") -> HagstofaBrowseResult:
    path = path.strip("/")
    base = _hagstofa_base(language)
    url = f"{base}{path}/" if path else base
    try:
        data = await _get_json(url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (400, 404):
            raise ToolError(
                f"No PX-Web folder at '{path}'. Browse from the top (empty path) and descend using each "
                "entry's full_path, or use search_hagstofa_tables."
            ) from None
        raise
    entries = []
    for item in data:
        # The API root uses {"dbid", "text"} with no "type" (always a folder);
        # every deeper level uses {"id", "type": "l"|"t", "text", ["updated"]}.
        item_id = item.get("id") or item["dbid"]
        entries.append(
            HagstofaBrowseEntry(
                id=item_id,
                entry_type="folder" if item.get("type", "l") == "l" else "table",
                title=item.get("text", ""),
                updated=item.get("updated"),
                full_path=f"{path}/{item_id}" if path else item_id,
            )
        )
    return HagstofaBrowseResult(path=path, entries=entries, source_url=url, retrieved_at=utc_now())


# ---------------------------------------------------------------------------
# island.is vehicle lookup (car)
# ---------------------------------------------------------------------------

ISLAND_IS_GRAPHQL = "https://island.is/api/graphql"

VEHICLE_SEARCH_QUERY = """
query($input: GetPublicVehicleSearchInput!) {
  publicVehicleSearch(input: $input) {
    permno regno vin make vehicleCommercialName color
    newRegDate firstRegDate vehicleStatus nextVehicleMainInspection
  }
}
"""


class VehicleResult(BaseModel):
    query: str
    vehicle: dict | None
    source_url: str = ISLAND_IS_GRAPHQL
    retrieved_at: str
    note: str = "island.is public vehicle registry lookup by exact plate or VIN. null means no match — the underlying search is not fuzzy/partial despite some documentation suggesting otherwise (verified empirically)."


async def get_vehicle(search: str) -> VehicleResult:
    variables = {"input": {"search": search}}
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.post(ISLAND_IS_GRAPHQL, json={"query": VEHICLE_SEARCH_QUERY, "variables": variables})
        response.raise_for_status()
        payload = response.json()
    if "errors" in payload:
        raise ValueError(f"island.is GraphQL error: {payload['errors']}")
    vehicle = payload["data"]["publicVehicleSearch"]
    return VehicleResult(query=search, vehicle=vehicle, retrieved_at=utc_now())


# ---------------------------------------------------------------------------
# Eurostat REST API (json-stat2)
# ---------------------------------------------------------------------------

EUROSTAT_BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"


class EurostatObservation(BaseModel):
    dimensions: dict[str, str]
    value: float | None
    flag: str | None = None


class EurostatSeriesResult(BaseModel):
    dataset: str
    filters: dict[str, str]
    observations: list[EurostatObservation]
    total_observations: int
    truncated: bool
    source_url: str
    retrieved_at: str
    note: str = (
        "Observations with value=null are published-as-missing; `flag` carries Eurostat's OBS_FLAG code when "
        "present (e.g. p=provisional, e=estimated, b=break in time series, c=confidential, :=not available)."
    )


def decode_eurostat_jsonstat(data: dict) -> tuple[list[EurostatObservation], int]:
    """Decode a json-stat2 message into observations; returns (first MAX_ROWS observations, total)."""
    dims = data.get("dimension", {})
    dim_ids = data.get("id", list(dims.keys()))
    sizes = data.get("size", [])
    # json-stat2: build index->label maps per dimension, then decode the composite
    # row-major index used by the flat "value" map (last dimension varies fastest).
    index_maps = []
    for dim_id in dim_ids:
        category = dims.get(dim_id, {}).get("category", {})
        index = category.get("index", {})
        label = category.get("label", {})
        pos_to_code = {v: k for k, v in index.items()} if isinstance(index, dict) else {}
        index_maps.append((dim_id, pos_to_code, label))

    values = data.get("value", {})
    if isinstance(values, list):  # json-stat also allows a dense array
        values = {str(i): v for i, v in enumerate(values) if v is not None}
    status = data.get("status", {})
    if isinstance(status, list):
        status = {str(i): v for i, v in enumerate(status) if v}
    # Flagged-but-missing observations (e.g. ':' not available) appear only in `status`.
    keys = sorted(set(values) | set(status), key=int)

    divisors = []
    acc = 1
    for size in reversed(sizes):
        divisors.append(acc)
        acc *= max(size, 1)
    divisors.reverse()

    observations: list[EurostatObservation] = []
    for flat_key in keys[:MAX_ROWS]:
        remainder = int(flat_key)
        dims_out = {}
        for (dim_id, pos_to_code, label), size, divisor in zip(index_maps, sizes, divisors):
            idx = (remainder // divisor) % max(size, 1)
            code = pos_to_code.get(idx, str(idx))
            dims_out[dim_id] = label.get(code, code) if isinstance(label, dict) else code
        raw = values.get(flat_key)
        observations.append(
            EurostatObservation(
                dimensions=dims_out,
                value=float(raw) if raw is not None else None,
                flag=status.get(flat_key),
            )
        )
    return observations, len(keys)


async def get_eurostat_series(
    dataset: str,
    filters: dict[str, str] | None = None,
    since_period: str | None = None,
    until_period: str | None = None,
    last_n_periods: int | None = None,
) -> EurostatSeriesResult:
    from .eurostat import eurostat_error_message, load_structure, validate_filters

    filters = filters or {}
    if last_n_periods is not None and (since_period or until_period):
        raise ToolError("Use either last_n_periods or since_period/until_period, not both.")
    dataset = dataset.strip().lower()
    structure = await load_structure(dataset)  # ToolError with a search hint if the dataset doesn't exist
    validate_filters(structure, {k: v for k, v in filters.items() if k.lower() != "time"})

    url = f"{EUROSTAT_BASE}{dataset}"
    # Eurostat takes several values for one dimension as repeated parameters (geo=IS&geo=DE);
    # callers write that as "IS+DE".
    params: list[tuple[str, str]] = [("format", "JSON"), ("lang", "EN")]
    for dim_id, value in filters.items():
        params.extend((dim_id, v) for v in str(value).split("+") if v)
    if since_period:
        params.append(("sinceTimePeriod", since_period))
    if until_period:
        params.append(("untilTimePeriod", until_period))
    if last_n_periods is not None:
        params.append(("lastTimePeriod", str(last_n_periods)))
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url, params=params)
    if response.status_code >= 400:
        message = eurostat_error_message(response)
        if message:
            hint = (
                " Narrow it with `filters` (see get_eurostat_dataset_info for dimensions) and since_period/last_n_periods."
                if response.status_code == 413
                else ""
            )
            raise ToolError(f"Eurostat error {response.status_code}: {message}.{hint}")
    response.raise_for_status()
    observations, total = decode_eurostat_jsonstat(response.json())

    return EurostatSeriesResult(
        dataset=dataset,
        filters=filters,
        observations=observations,
        total_observations=total,
        truncated=total > MAX_ROWS,
        source_url=str(httpx.URL(url, params=params)),
        retrieved_at=utc_now(),
    )


class EurostatSearchResult(BaseModel):
    query: str
    hits: list[EurostatSearchHit]
    source_url: str
    retrieved_at: str


async def search_eurostat_catalog(query: str, limit: int = 15) -> EurostatSearchResult:
    limit = max(1, min(limit, 50))
    entries = await load_toc()
    return EurostatSearchResult(
        query=query,
        hits=search_toc(entries, query, limit),
        source_url=f"{EUROSTAT_API}catalogue/toc/txt?lang=en",
        retrieved_at=utc_now(),
    )


class EurostatDimensionSummary(BaseModel):
    id: str
    position: int
    is_time: bool
    code_count: int
    sample_codes: dict[str, str]


class EurostatDatasetInfo(BaseModel):
    dataset: str
    title: str
    data_start: str | None
    data_end: str | None
    last_data_update: str | None
    dimensions: list[EurostatDimensionSummary]
    source_url: str
    retrieved_at: str
    note: str = (
        "Use each dimension `id` (lowercase) as a key in get_eurostat_series_tool's `filters`. Dimensions you omit "
        "return every value. The time dimension is set with since_period/until_period/last_n_periods instead."
    )


async def describe_eurostat_dataset(dataset: str) -> EurostatDatasetInfo:
    structure = await load_structure(dataset)
    toc = next((e for e in await load_toc() if e.code == structure.dataset), None)
    return EurostatDatasetInfo(
        dataset=structure.dataset,
        title=structure.title,
        data_start=toc.data_start if toc else None,
        data_end=toc.data_end if toc else None,
        last_data_update=toc.last_data_update if toc else None,
        dimensions=[
            EurostatDimensionSummary(
                id=d.id,
                position=d.position,
                is_time=d.is_time,
                code_count=len(d.codes),
                sample_codes=dict(list(d.codes.items())[:6]),
            )
            for d in structure.dimensions
        ],
        source_url=f"{EUROSTAT_API}sdmx/2.1/dataflow/ESTAT/{structure.dataset}/latest",
        retrieved_at=utc_now(),
    )


class EurostatDimensionValues(BaseModel):
    dataset: str
    dimension: str
    total_codes: int
    matching_codes: int
    values: dict[str, str]
    truncated: bool
    retrieved_at: str


async def list_eurostat_dimension_values(
    dataset: str, dimension: str, query: str | None = None, limit: int = 60
) -> EurostatDimensionValues:
    structure = await load_structure(dataset)
    dim = dimension_by_id(structure, dimension)
    if dim.is_time:
        raise ToolError("The time dimension has no code list; use since_period/until_period/last_n_periods instead.")
    codes = dim.codes
    if query:
        needle = query.casefold()
        codes = {c: l for c, l in codes.items() if needle in c.casefold() or needle in l.casefold()}
    limit = max(1, min(limit, 500))
    return EurostatDimensionValues(
        dataset=structure.dataset,
        dimension=dim.id,
        total_codes=len(dim.codes),
        matching_codes=len(codes),
        values=dict(list(codes.items())[:limit]),
        truncated=len(codes) > limit,
        retrieved_at=utc_now(),
    )


# ---------------------------------------------------------------------------
# Veðurstofa (weather + earthquakes)
# ---------------------------------------------------------------------------

VEDUR_WEATHER_BASE = "https://api.vedur.is/weather"
VEDUR_QUAKES_BASE = "https://api.vedur.is/quakes"


class WeatherObservationsResult(BaseModel):
    aggregation: str
    stations_returned: int
    observations: list[dict]
    truncated: bool
    source_url: str
    retrieved_at: str


async def get_weather_observations(aggregation: str = "10min", station_id: int | None = None) -> WeatherObservationsResult:
    if aggregation not in ("10min", "hour", "day", "month", "year"):
        raise ValueError("aggregation must be one of: 10min, hour, day, month, year")
    url = f"{VEDUR_WEATHER_BASE}/observations/aws/{aggregation}/latest"
    data = await _get_json(url)
    observations = data if isinstance(data, list) else data.get("data", data.get("results", []))
    if station_id is not None:
        observations = [o for o in observations if o.get("station") == station_id or o.get("station_id") == station_id]
    truncated = len(observations) > MAX_ROWS
    return WeatherObservationsResult(
        aggregation=aggregation,
        stations_returned=len(observations[:MAX_ROWS]),
        observations=observations[:MAX_ROWS],
        truncated=truncated,
        source_url=url,
        retrieved_at=utc_now(),
    )


class EarthquakeResult(BaseModel):
    start_time: str
    events: list[dict]
    truncated: bool
    source_url: str
    retrieved_at: str
    note: str = "IMO seismic events. No server-side limit is supported — always bound with start_time or you get the full history."


async def get_earthquakes(start_time: str, size_min: float | None = None, limit: int = 100) -> EarthquakeResult:
    params: dict = {"start_time": start_time}
    if size_min is not None:
        params["size_min"] = size_min
    data = await _get_json(f"{VEDUR_QUAKES_BASE}/events", params=params)
    features = data.get("features", []) if isinstance(data, dict) else data
    limit = max(1, min(limit, MAX_ROWS))
    return EarthquakeResult(
        start_time=start_time,
        events=features[:limit],
        truncated=len(features) > limit,
        source_url=f"{VEDUR_QUAKES_BASE}/events",
        retrieved_at=utc_now(),
    )


# ---------------------------------------------------------------------------
# Air quality (UST)
# ---------------------------------------------------------------------------

UST_AQ_BASE = "https://api.ust.is/aq/a"


class AirQualityResult(BaseModel):
    date: str | None = None
    stations: dict
    source_url: str
    retrieved_at: str
    note: str = "Values arrive as strings in the upstream API — cast to float yourself. Readings above ~2000 are typically instrument faults, not real air quality."


async def get_air_quality(date: str | None = None, station_local_id: str | None = None) -> AirQualityResult:
    if date:
        url = f"{UST_AQ_BASE}/getDate/date/{date}"
    elif station_local_id:
        url = f"{UST_AQ_BASE}/getCurrent/{station_local_id}"
    else:
        url = f"{UST_AQ_BASE}/getLatest"
    data = await _get_json(url)
    return AirQualityResult(date=date, stations=data, source_url=url, retrieved_at=utc_now())


# ---------------------------------------------------------------------------
# Lánamál ríkisins — government bond yields
# ---------------------------------------------------------------------------

LANAMAL_BASE = "https://www.lanamal.is/api/market/LoadIndexedDetail"


class BondResult(BaseModel):
    orderbook_id: str
    short_name: str | None = None
    long_name: str | None = None
    attributes: dict[str, str]
    latest_yield_fixing: dict | None = None
    source_url: str
    retrieved_at: str
    note: str = (
        "The top-level closingYield/bidYield/askYield fields reflect the last actual trade, which can be stale "
        "for thinly-traded bonds — latest_yield_fixing (the daily market-maker fixing) is the better 'yield today' answer."
    )


async def get_bond(orderbook_id: str) -> BondResult:
    headers = {"Referer": f"https://www.lanamal.is/markadsyfirlit/?type=bond&orderbookid={orderbook_id.lower()}"}
    data = await _get_json(LANAMAL_BASE, params={"orderbookId": orderbook_id, "lang": "is"}, headers=headers)
    if not data:
        raise ValueError(f"No bond found for orderbook id {orderbook_id!r}.")
    item = data[0]
    attrs = {a["name"]: a["value"] for a in item.get("attributes", [])}
    chart = item.get("chartData", {}).get("chartData", [])
    latest = None
    if chart:
        latest = {"date": chart[-1][0], "yield": chart[-1][1]}
    return BondResult(
        orderbook_id=item.get("orderbookId", orderbook_id),
        short_name=item.get("shortName"),
        long_name=item.get("longName"),
        attributes=attrs,
        latest_yield_fixing=latest,
        source_url=f"{LANAMAL_BASE}?orderbookId={orderbook_id}",
        retrieved_at=utc_now(),
    )


# ---------------------------------------------------------------------------
# Ríkisreikningur — state accounts actuals
# ---------------------------------------------------------------------------

RIKISREIKNINGUR_BASE = "https://rikisreikningurapi.azurewebsites.net"
# NOT A SECRET despite the header name: this is a static anonymous-throttling
# key, not an access credential. It ships in rikisreikningur.is's own public,
# unauthenticated JS bundle and is sent to every visitor of that site with no
# login — verified directly (not just taken on faith) by fetching that bundle
# and finding this exact string in it: curl -s https://www.rikisreikningur.is/
# | grep -o 'main[^"]*\.js', then curl that chunk and grep for the key.
# Hardcoding it here is correct; treating it as a secret would only break this
# tool for no security benefit, since it's already public to anyone who opens
# their browser's devtools on that site.
RIKISREIKNINGUR_API_KEY = "6d4d7394-2992-473d-9ea7-45946b39ad9d"


class RikisreikningurSummary(BaseModel):
    current_period: dict
    afkoma_by_year: list[dict]
    tekjur_gjold: list[dict]
    source_url: str
    retrieved_at: str
    note: str = "Yearly government-wide surplus/deficit and revenue/expense split. See get_rikisreikningur_malefni for the per-policy-area breakdown."


async def get_rikisreikningur_summary() -> RikisreikningurSummary:
    headers = {"X-Api-Key": RIKISREIKNINGUR_API_KEY}
    current = await _get_json(f"{RIKISREIKNINGUR_BASE}/api/FJS/NuverandiTimabil", headers=headers)
    tg = await _get_json(f"{RIKISREIKNINGUR_BASE}/api/FJS/TekjurOgGjold", headers=headers)
    return RikisreikningurSummary(
        current_period=current,
        afkoma_by_year=tg.get("afkoma", []),
        tekjur_gjold=tg.get("tekjur_gjold", []),
        source_url=f"{RIKISREIKNINGUR_BASE}/api/FJS/TekjurOgGjold",
        retrieved_at=utc_now(),
    )


class RikisreikningurMalefniResult(BaseModel):
    rows: list[dict]
    truncated: bool
    source_url: str
    retrieved_at: str
    note: str = (
        "Revenue/expense by málefnasvið (policy area), year and type. Filter client-side on malefnasvid_numer "
        "if you only need one policy area — the upstream endpoint returns the full ~620-row table."
    )


async def get_rikisreikningur_malefni() -> RikisreikningurMalefniResult:
    headers = {"X-Api-Key": RIKISREIKNINGUR_API_KEY}
    url = f"{RIKISREIKNINGUR_BASE}/api/FJS/Data/malefni_tg"
    data = await _get_json(url, headers=headers)
    # This endpoint double-encodes: a one-element list containing a JSON string.
    inner = json.loads(data[0]) if isinstance(data, list) and data else {}
    rows = inner.get("malefni_tg", [])
    truncated = len(rows) > MAX_ROWS
    return RikisreikningurMalefniResult(rows=rows[:MAX_ROWS], truncated=truncated, source_url=url, retrieved_at=utc_now())


# ---------------------------------------------------------------------------
# Opnir reikningar — government invoice data
# ---------------------------------------------------------------------------

OPNIRREIKNINGAR_BASE = "https://opnirreikningar.is"


class InvoiceSearchResult(BaseModel):
    invoices: list[dict]
    truncated: bool
    source_url: str
    retrieved_at: str
    note: str = "Excludes salaries, foreign-currency transactions, benefits, healthcare-provider payments, prisoner payments, security operations, and municipality data (central government only)."


async def search_invoices(
    date_from: str, date_to: str, org_id: str | None = None, limit: int = 100
) -> InvoiceSearchResult:
    limit = max(1, min(limit, MAX_ROWS))
    params = {
        "vendor_id": "",
        "type_id": "",
        "org_id": org_id or "",
        "timabil_fra": date_from,
        "timabil_til": date_to,
        "draw": 1,
        "columns[0][data]": "org_name",
        "columns[1][data]": "check_date",
        "columns[2][data]": "vendor_name",
        "columns[3][data]": "invoice_amount",
        "columns[4][data]": "check_amount",
        "start": 0,
        "length": limit,
        "order[0][column]": 1,
        "order[0][dir]": "desc",
    }
    headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
    data = await _get_json(f"{OPNIRREIKNINGAR_BASE}/data_pagination_search", params=params, headers=headers)
    invoices = data.get("data", [])
    return InvoiceSearchResult(
        invoices=invoices,
        truncated=len(invoices) >= limit,
        source_url=f"{OPNIRREIKNINGAR_BASE}/data_pagination_search",
        retrieved_at=utc_now(),
    )


class OrgSearchResult(BaseModel):
    matches: list[dict]
    source_url: str
    retrieved_at: str


async def search_invoice_orgs(term: str) -> OrgSearchResult:
    data = await _get_json(f"{OPNIRREIKNINGAR_BASE}/rest/org", params={"term": term})
    return OrgSearchResult(
        matches=data.get("data", []), source_url=f"{OPNIRREIKNINGAR_BASE}/rest/org", retrieved_at=utc_now()
    )


# ---------------------------------------------------------------------------
# Skipulagsmál — Planitor planning/building permits
# ---------------------------------------------------------------------------

PLANITOR_BASE = "https://www.planitor.io/api"


class PlanningSearchResult(BaseModel):
    query: str
    minutes: list[dict]
    source_url: str
    retrieved_at: str
    note: str = "Covers Reykjavík, Hafnarfjörður and Árborg only. No structured permit-type field — classification is by free-text matching on the 'inquiry' field."


async def search_planning_minutes(query: str, limit: int = 20) -> PlanningSearchResult:
    limit = max(1, min(limit, 200))
    data = await _get_json(f"{PLANITOR_BASE}/minutes/search", params={"q": query, "limit": limit})
    return PlanningSearchResult(
        query=query,
        minutes=data.get("items", []),
        source_url=f"{PLANITOR_BASE}/minutes/search",
        retrieved_at=utc_now(),
    )


class NearbyCasesResult(BaseModel):
    lat: float
    lon: float
    radius_m: int
    cases: list[dict]
    source_url: str
    retrieved_at: str


async def get_nearby_planning_cases(lat: float, lon: float, radius_m: int = 500, limit: int = 100) -> NearbyCasesResult:
    radius_m = max(1, min(radius_m, 5000))
    limit = max(1, min(limit, 500))
    data = await _get_json(
        f"{PLANITOR_BASE}/cases/nearby", params={"lat": lat, "lon": lon, "radius_m": radius_m, "limit": limit}
    )
    return NearbyCasesResult(
        lat=lat,
        lon=lon,
        radius_m=radius_m,
        cases=data.get("items", []),
        source_url=f"{PLANITOR_BASE}/cases/nearby",
        retrieved_at=utc_now(),
    )


# ---------------------------------------------------------------------------
# Heimsmarkmiðin — Iceland's UN SDG indicators
# ---------------------------------------------------------------------------

HEIMSMARKMID_BASE = "https://hagstofan.github.io/heimsmarkmid-data-prod"


class SdgIndicatorResult(BaseModel):
    code: str
    columns: list[str]
    rows: list[list[str]]
    truncated: bool
    source_url: str
    retrieved_at: str


async def get_sdg_indicator(code: str, lang: str = "is") -> SdgIndicatorResult:
    if lang not in ("is", "en"):
        raise ValueError("lang must be 'is' or 'en'.")
    url = f"{HEIMSMARKMID_BASE}/{lang}/data/{code}.csv"
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.content.decode(resp.encoding or "utf-8")
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if row]
    header, body = (rows[0], rows[1:]) if rows else ([], [])
    truncated = len(body) > MAX_ROWS
    return SdgIndicatorResult(
        code=code, columns=header, rows=body[:MAX_ROWS], truncated=truncated, source_url=url, retrieved_at=utc_now()
    )


# ---------------------------------------------------------------------------
# TED — EU public procurement notices
# ---------------------------------------------------------------------------

TED_SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"


class TenderSearchResult(BaseModel):
    query: str
    total_notice_count: int | None
    notices: list[dict]
    source_url: str = TED_SEARCH_URL
    retrieved_at: str
    note: str = (
        "Only EEA-threshold notices are covered (~1,481 for Iceland historically) — this is a thin pass-through "
        "of TED's own v3 response; which requested 'fields' actually populate can be inconsistent upstream."
    )


async def search_tenders(query: str, fields: list[str] | None = None, limit: int = 20, page: int = 1) -> TenderSearchResult:
    limit = max(1, min(limit, 100))
    default_fields = ["notice-title", "publication-date", "organisation-name-buyer", "tender-value", "tender-value-cur"]
    payload = {"query": query, "fields": fields or default_fields, "limit": limit, "page": page}
    data = await _post_json(TED_SEARCH_URL, payload)
    return TenderSearchResult(
        query=query,
        total_notice_count=data.get("totalNoticeCount"),
        notices=data.get("notices", []),
        retrieved_at=utc_now(),
    )


# ---------------------------------------------------------------------------
# EEA SDI — European Environment Agency geospatial catalogue
# ---------------------------------------------------------------------------

EEA_SDI_SEARCH_URL = "https://sdi.eea.europa.eu/catalogue/srv/api/search/records/_search"


class EeaCatalogueHit(BaseModel):
    uuid: str
    title: str | None = None
    publication_year: str | None = None


class EeaCatalogueSearchResult(BaseModel):
    query: str
    total: int
    hits: list[EeaCatalogueHit]
    source_url: str = EEA_SDI_SEARCH_URL
    retrieved_at: str
    note: str = "Catalogue search only — the underlying data is often a large raster on EEA's discomap ArcGIS server, or requires a free Copernicus Land account."


async def search_eea_datasets(query: str, limit: int = 10) -> EeaCatalogueSearchResult:
    limit = max(1, min(limit, 50))
    payload = {
        "query": {"bool": {"must": [{"match": {"resourceTitleObject.langeng": query}}]}},
        "_source": ["uuid", "resourceTitleObject.default", "publicationYearForResource"],
    }
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as client:
        resp = await client.post(f"{EEA_SDI_SEARCH_URL}?from=0&size={limit}", json=payload)
        resp.raise_for_status()
        data = resp.json()
    hits_raw = data.get("hits", {}).get("hits", [])
    hits = [
        EeaCatalogueHit(
            uuid=h["_source"].get("uuid", h.get("_id", "")),
            title=h["_source"].get("resourceTitleObject", {}).get("default"),
            publication_year=h["_source"].get("publicationYearForResource"),
        )
        for h in hits_raw
    ]
    return EeaCatalogueSearchResult(
        query=query, total=data.get("hits", {}).get("total", {}).get("value", 0), hits=hits, retrieved_at=utc_now()
    )


# ---------------------------------------------------------------------------
# Historical FX rates (frankfurter.dev — ECB reference rates)
# ---------------------------------------------------------------------------

FRANKFURTER_BASE = "https://api.frankfurter.dev/v1"


class FxRateResult(BaseModel):
    date: str
    base: str
    rates: dict[str, float]
    source_url: str
    retrieved_at: str
    note: str = "ECB daily reference rates (interbank), not consumer card rates. ISK is not an ECB reference currency for the base side in practice — pass base='EUR' or another major currency and read ISK from rates if you need ISK cross rates."


async def get_fx_rate(date: str = "latest", base: str = "EUR", symbols: str | None = None) -> FxRateResult:
    params: dict = {"base": base}
    if symbols:
        params["symbols"] = symbols
    url = f"{FRANKFURTER_BASE}/{date}"
    data = await _get_json(url, params=params)
    return FxRateResult(
        date=data.get("date", date), base=data.get("base", base), rates=data.get("rates", {}), source_url=url, retrieved_at=utc_now()
    )


# ---------------------------------------------------------------------------
# island.is registered web-service catalogue (Straumur/X-Road reference index)
# ---------------------------------------------------------------------------
#
# Ported from a separate PoC, island-is-mcp (same author) — see that project's
# docs/discovery-notes.md for the original discovery notes this section is
# based on. Re-verified live before porting: the catalogue's own `query` input
# does server-side title/owner filtering (confirmed by testing, not assumed
# from the original project, which had built a local crawl+cache instead of
# relying on this) — so unlike that project, no local index/cache is needed
# here; every call is live, consistent with this codebase's style elsewhere.
#
# CRITICAL: this catalogue is REFERENCE METADATA ONLY. It lists which web
# services are registered — owner, access path, declared data sensitivity,
# and (when published) OpenAPI documentation — but every one of the ~120
# services listed requires X-Road membership to actually call, including
# ones tagged APIGW (verified: all APIGW-tagged services also carry XROAD;
# none are self-serve). These tools can never return real registry data
# (þjóðskrá, ökutækjaskrá, fasteignaskrá, health records, etc.) and must
# never be presented as if they could.

MAX_TEXT_CHARS_CATALOGUE = 50_000

CATALOGUE_QUERY = """
query GetApiCatalogue($input: GetApiCatalogueInput!) {
  getApiCatalogue(input: $input) {
    services {
      id
      title
      owner
      pricing
      type
      access
      data
      environments { environment }
    }
    pageInfo { nextCursor }
  }
}
"""

OPENAPI_QUERY = """
query GetOpenApi($input: GetOpenApiInput!) {
  getOpenApi(input: $input) { spec }
}
"""


class ApiCatalogueService(BaseModel):
    service_id: str
    title: str
    owner: str
    pricing: list[str]
    type: list[str]
    access: list[str]
    data: list[str]


class ApiCatalogueSearchResult(BaseModel):
    query: str
    returned: int
    services: list[ApiCatalogueService]
    note: str = (
        "REFERENCE METADATA ONLY. This lists registered web services on Straumur/api.island.is — it never "
        "returns real registry data (þjóðskrá, ökutækjaskrá, fasteignaskrá, health records, etc.). Every "
        "listed service, including ones tagged 'APIGW', requires direct X-Road membership with the data "
        "owner to actually call — there is no self-serve path. Use get_webservice_details for one service's "
        "full metadata, list_service_endpoints/get_service_openapi_spec for its documented operations."
    )


class XRoadIdentity(BaseModel):
    instance: str
    member_class: str
    member_code: str
    subsystem_code: str
    service_code: str


def _decode_service_id(service_id: str) -> XRoadIdentity | None:
    """Decode getApiCatalogue's `id` into the X-Road identity getOpenApi needs.

    `id` is base64 of "{instance}_{memberClass}_{memberCode}_{subsystemCode}_{serviceCode}"
    (verified against all ~120 live entries when this was first built). Assumes
    subsystemCode never contains an underscore (observed: it uses hyphens, e.g.
    "EmbaettiLandlaeknis-Protected") while serviceCode sometimes does (e.g.
    "TBRInfo_v1") — so the first four underscore-separated parts are taken as
    instance/memberClass/memberCode/subsystemCode and everything after is
    rejoined as serviceCode. Returns None rather than guessing wrong if decoding
    fails outright.
    """
    padded = service_id + "=" * (-len(service_id) % 4)
    try:
        decoded = base64.b64decode(padded).decode("utf-8")
    except Exception:
        return None
    parts = decoded.split("_")
    if len(parts) < 5:
        return None
    instance, member_class, member_code, subsystem_code = parts[:4]
    return XRoadIdentity(
        instance=instance,
        member_class=member_class,
        member_code=member_code,
        subsystem_code=subsystem_code,
        service_code="_".join(parts[4:]),
    )


async def _fetch_catalogue_page(query: str, cursor: str | None, limit: int) -> dict:
    variables = {
        "input": {
            "cursor": cursor,
            "limit": limit,
            "query": query,
            "pricing": [],
            "data": [],
            "type": [],
            "access": [],
        }
    }
    data = await _post_json(ISLAND_IS_GRAPHQL, {"query": CATALOGUE_QUERY, "variables": variables})
    if "errors" in data:
        raise ValueError(f"island.is GraphQL error: {data['errors']}")
    return data["data"]["getApiCatalogue"]


def _to_service(raw: dict) -> ApiCatalogueService:
    return ApiCatalogueService(
        service_id=raw["id"],
        title=raw["title"],
        owner=raw["owner"],
        pricing=raw.get("pricing", []),
        type=raw.get("type", []),
        access=raw.get("access", []),
        data=raw.get("data", []),
    )


async def search_webservices(query: str = "", limit: int = 20) -> ApiCatalogueSearchResult:
    limit = max(1, min(limit, 100))
    result = await _fetch_catalogue_page(query, cursor=None, limit=limit)
    services = [_to_service(s) for s in result["services"]]
    return ApiCatalogueSearchResult(query=query, returned=len(services), services=services)


class WebserviceDetail(ApiCatalogueService):
    environments: list[str]
    openapi_available: bool = False
    description: str | None = None
    contact: dict | None = None
    documentation_url: str | None = None
    xroad_identity: XRoadIdentity | None = None
    note: str = (
        "REFERENCE METADATA ONLY — see ApiCatalogueSearchResult.note. This is never real registry data."
    )


async def get_webservice_details(service_id: str) -> WebserviceDetail:
    # No by-id catalogue query exists upstream (confirmed via GraphQL's own "did
    # you mean" validation error) — one page at limit=500 covers the whole
    # catalogue (~120 services, confirmed live) in a single round trip, so a
    # local scan is cheap and needs no persistent cache.
    result = await _fetch_catalogue_page("", cursor=None, limit=500)
    raw = next((s for s in result["services"] if s["id"] == service_id), None)
    if raw is None:
        raise ToolError(
            f"No service found with service_id={service_id!r}. Call search_webservices to find valid ids."
        )

    environments = [e["environment"] for e in raw.get("environments", [])]
    identity = _decode_service_id(service_id)
    detail = WebserviceDetail(**_to_service(raw).model_dump(), environments=environments, xroad_identity=identity)

    if identity is None:
        return detail

    spec_data = await _post_json(
        ISLAND_IS_GRAPHQL,
        {
            "query": OPENAPI_QUERY,
            "variables": {
                "input": {
                    "instance": identity.instance,
                    "memberCode": identity.member_code,
                    "memberClass": identity.member_class,
                    "subsystemCode": identity.subsystem_code,
                    "serviceCode": identity.service_code,
                }
            },
        },
    )
    spec_text = spec_data.get("data", {}).get("getOpenApi", {}).get("spec") or ""
    if not spec_text:
        return detail
    try:
        spec = json.loads(spec_text)
    except json.JSONDecodeError:
        return detail
    info = spec.get("info") if isinstance(spec, dict) else None
    info = info if isinstance(info, dict) else {}
    contact = info.get("contact")
    x_links = info.get("x-links")
    detail.openapi_available = True
    detail.description = info.get("description") if isinstance(info.get("description"), str) else None
    detail.contact = contact if isinstance(contact, dict) else None
    detail.documentation_url = x_links.get("documentation") if isinstance(x_links, dict) else None
    return detail


class ServiceEndpoint(BaseModel):
    path: str
    method: str
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)


class ServiceEndpointsResult(BaseModel):
    service_id: str
    title: str
    endpoints: list[ServiceEndpoint]
    note: str = (
        "Documentation of what the service's own published OpenAPI spec says it can do — not a tool that "
        "calls anything. Actually invoking any of these operations requires direct X-Road access to the "
        "data owner; this server has no such access and never will."
    )


def _extract_endpoints(spec: dict) -> list[ServiceEndpoint]:
    endpoints: list[ServiceEndpoint] = []
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return endpoints
    for path, operations in paths.items():
        if not isinstance(operations, dict):
            continue
        for method, op in operations.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete") or not isinstance(op, dict):
                continue
            endpoints.append(
                ServiceEndpoint(
                    path=path,
                    method=method.upper(),
                    summary=op.get("summary") if isinstance(op.get("summary"), str) else None,
                    tags=op.get("tags") if isinstance(op.get("tags"), list) else [],
                )
            )
    return endpoints


async def _get_service_spec(service_id: str) -> tuple[str, dict | None]:
    """Returns (title, spec-or-None). Shared by list_service_endpoints/get_service_openapi_spec."""
    detail = await get_webservice_details(service_id)
    if not detail.openapi_available:
        return detail.title, None
    identity = detail.xroad_identity
    spec_data = await _post_json(
        ISLAND_IS_GRAPHQL,
        {
            "query": OPENAPI_QUERY,
            "variables": {
                "input": {
                    "instance": identity.instance,
                    "memberCode": identity.member_code,
                    "memberClass": identity.member_class,
                    "subsystemCode": identity.subsystem_code,
                    "serviceCode": identity.service_code,
                }
            },
        },
    )
    spec_text = spec_data.get("data", {}).get("getOpenApi", {}).get("spec") or ""
    try:
        spec = json.loads(spec_text) if spec_text else None
    except json.JSONDecodeError:
        spec = None
    return detail.title, spec if isinstance(spec, dict) else None


async def list_service_endpoints(service_id: str) -> ServiceEndpointsResult:
    title, spec = await _get_service_spec(service_id)
    endpoints = _extract_endpoints(spec) if spec else []
    return ServiceEndpointsResult(service_id=service_id, title=title, endpoints=endpoints)


class ServiceOpenApiSpecResult(BaseModel):
    service_id: str
    title: str
    spec: dict | None
    spec_size_chars: int | None = None
    note: str = "Documentation only, same caveat as ServiceEndpointsResult.note."


async def get_service_openapi_spec(service_id: str) -> ServiceOpenApiSpecResult:
    title, spec = await _get_service_spec(service_id)
    if spec is None:
        return ServiceOpenApiSpecResult(service_id=service_id, title=title, spec=None)
    spec_size = len(json.dumps(spec, ensure_ascii=False))
    if spec_size > MAX_TEXT_CHARS_CATALOGUE:
        return ServiceOpenApiSpecResult(
            service_id=service_id,
            title=title,
            spec=None,
            spec_size_chars=spec_size,
            note=(
                f"Spec too large to return in full ({spec_size} chars) — use list_service_endpoints for a "
                "compact summary of its operations instead."
            ),
        )
    return ServiceOpenApiSpecResult(service_id=service_id, title=title, spec=spec, spec_size_chars=spec_size)


# ---------------------------------------------------------------------------
# island.is public web content (guides, life events, organization pages)
# ---------------------------------------------------------------------------
#
# Also ported from island-is-mcp — see that project's docs/content-connector-
# notes.md. Unlike the legal/EEA sources in sources.py, this content is
# editorial/informational (how-to guides, procedures), not law — no
# authority-class applies, same as the rest of this module. The queries
# (getSingleArticle, searchResults with SearcherInput) are not visible in
# browser network traffic (the pages are server-rendered) — they were found
# by probing Apollo Server's schema-validation error messages ("did you mean
# X?"), which remain informative even though introspection itself is disabled
# server-side. Re-verified live before porting.

CONTENT_SEARCH_QUERY = """
query GetSearchResults($query: SearcherInput!) {
  searchResults(query: $query) {
    total
    items {
      __typename
      ... on Article { id title articleSlug: slug intro }
      ... on LifeEventPage { id title lifeEventSlug: slug intro }
      ... on Manual { id title manualSlug: slug }
      ... on OrganizationSubpage { id title orgSubSlug: slug }
    }
  }
}
"""

GET_ARTICLE_QUERY = """
query GetSingleArticle($input: GetSingleArticleInput!) {
  getSingleArticle(input: $input) {
    id
    title
    slug
    intro
    contentLastReviewed
    body {
      __typename
      ... on Html { document }
      ... on ProcessEntry { id processTitle buttonText }
    }
  }
}
"""

CONTENT_SEARCH_TYPES = ["webArticle", "webLifeEventPage", "webManual", "webOrganizationSubpage"]
CONTENT_TYPE_LABELS = {
    "Article": "article",
    "LifeEventPage": "life event",
    "Manual": "manual",
    "OrganizationSubpage": "organization subpage",
}
CONTENT_SLUG_FIELDS = {
    "Article": "articleSlug",
    "LifeEventPage": "lifeEventSlug",
    "Manual": "manualSlug",
    "OrganizationSubpage": "orgSubSlug",
}
MAX_TEXT_CHARS_CONTENT = 50_000


class ContentSearchHit(BaseModel):
    id: str
    type: str
    type_label: str
    title: str
    slug: str | None = None
    intro: str | None = None
    full_text_available: bool


class ContentSearchResult(BaseModel):
    query: str
    total_matches: int
    returned: int
    items: list[ContentSearchHit]
    note: str = (
        "Public island.is guidance content (how-to articles, life events, organization pages), not law and "
        "not registry data. full_text_available=true (type='Article' only, for now) means get_island_article "
        "can retrieve the full body; other types currently surface only title/intro here."
    )


async def search_island_content(query: str, lang: str = "is", limit: int = 10) -> ContentSearchResult:
    limit = max(1, min(limit, 30))
    variables = {"query": {"queryString": query, "language": lang, "types": CONTENT_SEARCH_TYPES}}
    data = await _post_json(ISLAND_IS_GRAPHQL, {"query": CONTENT_SEARCH_QUERY, "variables": variables})
    if "errors" in data:
        raise ValueError(f"island.is GraphQL error: {data['errors']}")
    result = data["data"]["searchResults"]
    items = []
    for item in result["items"][:limit]:
        type_name = item["__typename"]
        slug_field = CONTENT_SLUG_FIELDS.get(type_name)
        items.append(
            ContentSearchHit(
                id=item["id"],
                type=type_name,
                type_label=CONTENT_TYPE_LABELS.get(type_name, type_name),
                title=item["title"],
                slug=item.get(slug_field) if slug_field else None,
                intro=item.get("intro"),
                full_text_available=type_name == "Article",
            )
        )
    return ContentSearchResult(query=query, total_matches=result["total"], returned=len(items), items=items)


def _richtext_to_markdown(node: dict) -> str:
    node_type = node.get("nodeType")
    if node_type == "text":
        value = node.get("value", "")
        for mark in node.get("marks", []):
            wrapper = {"bold": "**", "italic": "_", "code": "`"}.get(mark.get("type"))
            if wrapper:
                value = f"{wrapper}{value}{wrapper}"
        return value
    children = "".join(_richtext_to_markdown(c) for c in node.get("content", []))
    if node_type == "hyperlink":
        uri = (node.get("data") or {}).get("uri", "")
        return f"[{children}]({uri})" if uri else children
    if node_type == "paragraph":
        return children + "\n\n"
    if node_type and node_type.startswith("heading-"):
        level = node_type.split("-")[-1]
        hashes = "#" * int(level) if level.isdigit() else "###"
        return f"{hashes} {children}\n\n"
    if node_type == "list-item":
        return f"- {children.strip()}\n"
    if node_type in ("unordered-list", "ordered-list"):
        return children + "\n"
    if node_type == "hr":
        return "---\n\n"
    return children


class ArticleResult(BaseModel):
    title: str
    slug: str
    intro: str | None = None
    content_last_reviewed: str | None = None
    body_markdown: str
    body_truncated: bool = False
    source_url: str
    note: str = "Public island.is guidance content — editorial/informational, not law and not registry data."


async def get_island_article(slug: str, lang: str = "is") -> ArticleResult:
    variables = {"input": {"slug": slug, "lang": lang}}
    data = await _post_json(ISLAND_IS_GRAPHQL, {"query": GET_ARTICLE_QUERY, "variables": variables})
    if "errors" in data:
        raise ValueError(f"island.is GraphQL error: {data['errors']}")
    article = data["data"]["getSingleArticle"]
    if article is None:
        raise ToolError(f"No article found with slug={slug!r}.")

    parts = []
    for block in article.get("body", []):
        if block["__typename"] == "Html":
            text = _richtext_to_markdown(block.get("document") or {}).strip()
            if text:
                parts.append(text)
        elif block["__typename"] == "ProcessEntry":
            title = block.get("processTitle")
            button = block.get("buttonText")
            if title or button:
                parts.append(f"[Action: {title or ''} — {button or ''}]".strip())

    body_markdown = "\n\n".join(parts)
    truncated = len(body_markdown) > MAX_TEXT_CHARS_CONTENT
    if truncated:
        body_markdown = body_markdown[:MAX_TEXT_CHARS_CONTENT]

    return ArticleResult(
        title=article["title"],
        slug=article["slug"],
        intro=article.get("intro"),
        content_last_reviewed=article.get("contentLastReviewed"),
        body_markdown=body_markdown,
        body_truncated=truncated,
        source_url=f"https://island.is/{article['slug']}",
    )
