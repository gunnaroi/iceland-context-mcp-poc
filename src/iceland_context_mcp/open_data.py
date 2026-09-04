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
    version = "2.0.0" if source_key == "lmi" else "1.0.0"
    params = {
        "service": "WFS",
        "version": version,
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
