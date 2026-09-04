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

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

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


class StatTableResult(BaseModel):
    table_path: str
    columns: list[str]
    rows: list[list[str]]
    truncated: bool
    source_url: str
    retrieved_at: str
    note: str = "Hagstofa Íslands PX-Web table, fetched as CSV and parsed. Values are as published — check the table's own unit/scale conventions (e.g. thousands of ISK) before using them."


async def get_hagstofa_table(table_path: str, filters: dict[str, list[str]] | None = None) -> StatTableResult:
    table_path = table_path.strip("/")
    url = f"{HAGSTOFA_BASE}{table_path}"
    query = [{"code": code, "selection": {"filter": "item", "values": values}} for code, values in (filters or {}).items()]
    payload = {"query": query, "response": {"format": "csv"}}
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        # The declared charset varies by table (seen: UTF-8 with BOM, Windows-1252) —
        # trust the response's own content-type rather than assuming one encoding.
        text = response.content.decode(response.encoding or "utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if row]
    header, body = (rows[0], rows[1:]) if rows else ([], [])
    truncated = len(body) > MAX_ROWS
    return StatTableResult(
        table_path=table_path,
        columns=header,
        rows=body[:MAX_ROWS],
        truncated=truncated,
        source_url=url,
        retrieved_at=utc_now(),
    )


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
    value: float


class EurostatSeriesResult(BaseModel):
    dataset: str
    filters: dict[str, str]
    observations: list[EurostatObservation]
    truncated: bool
    source_url: str
    retrieved_at: str


async def get_eurostat_series(dataset: str, filters: dict[str, str] | None = None) -> EurostatSeriesResult:
    url = f"{EUROSTAT_BASE}{dataset}"
    params = {"format": "JSON", **(filters or {})}
    data = await _get_json(url, params=params)
    dims = data.get("dimension", {})
    dim_ids = data.get("id", list(dims.keys()))
    sizes = data.get("size", [])
    # json-stat2: build index->label maps per dimension, then decode the
    # composite row-major index used by the flat "value" map.
    index_maps = []
    for dim_id in dim_ids:
        category = dims.get(dim_id, {}).get("category", {})
        index = category.get("index", {})
        label = category.get("label", {})
        # index maps code->position; invert to position->(code,label)
        pos_to_code = {v: k for k, v in index.items()} if isinstance(index, dict) else {}
        index_maps.append((dim_id, pos_to_code, label))

    values = data.get("value", {})
    observations: list[EurostatObservation] = []
    count = 0
    for flat_key, value in values.items():
        if count >= MAX_ROWS:
            break
        pos = int(flat_key)
        dims_out = {}
        remainder = pos
        # row-major, first dimension varies fastest per json-stat2
        divisors = []
        acc = 1
        for size in sizes:
            divisors.append(acc)
            acc *= max(size, 1)
        for (dim_id, pos_to_code, label), size, divisor in zip(index_maps, sizes, divisors):
            idx = (remainder // divisor) % max(size, 1)
            code = pos_to_code.get(idx, str(idx))
            dims_out[dim_id] = label.get(code, code) if isinstance(label, dict) else code
        observations.append(EurostatObservation(dimensions=dims_out, value=float(value)))
        count += 1

    return EurostatSeriesResult(
        dataset=dataset,
        filters=filters or {},
        observations=observations,
        truncated=len(values) > MAX_ROWS,
        source_url=str(httpx.URL(url, params=params)),
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
