"""Cached crawl of Hagstofa's PX-Web folder tree, for keyword table search.

PX-Web v1 (all Hagstofa hosts) has no search endpoint, only per-folder listings, and folder
ids are Icelandic abbreviations that can't be guessed. This module walks the whole tree once
(~1,000 folder requests, throttled — the server answers 429 to concurrent bursts), keeps a
flat index of every table with its folder-title breadcrumb, and searches that locally.

Snapshot sources, newest wins: a runtime cache file (refreshed in the background when older
than CATALOG_MAX_AGE_DAYS) and a seed shipped in the package (`hagstofa_catalog.json`,
rebuilt with the `iceland-context-hagstofa-crawl` command).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel

HAGSTOFA_API = "https://px.hagstofa.is/pxis/api/v1/is/"
USER_AGENT = "IcelandTrustedContextMCPPoC/0.1 (+public research proof of concept)"
SEED_PATH = Path(__file__).with_name("hagstofa_catalog.json")
CATALOG_MAX_AGE_DAYS = 14
MIN_REQUEST_INTERVAL = 0.3
MAX_RETRIES = 6


def cache_path() -> Path:
    base = os.environ.get("ICELAND_MCP_CACHE_DIR") or (Path.home() / ".cache" / "iceland-context-mcp")
    return Path(base) / "hagstofa_catalog.json"


class CatalogTable(BaseModel):
    path: str
    title: str
    updated: str | None = None
    breadcrumb: list[str]


class CatalogSnapshot(BaseModel):
    crawled_at: str
    folders_crawled: int
    tables: list[CatalogTable]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def crawl_catalog(progress=None) -> CatalogSnapshot:
    """Walk the whole tree. Raises RuntimeError if any folder could not be read, so a partial
    crawl never replaces a good snapshot."""
    tables: list[CatalogTable] = []
    failed: list[str] = []
    folders = 0
    lock = asyncio.Lock()
    last_request = 0.0

    async def fetch(client: httpx.AsyncClient, url: str) -> list[dict] | None:
        nonlocal last_request
        for attempt in range(MAX_RETRIES):
            async with lock:  # serialises requests and enforces the minimum spacing
                wait = MIN_REQUEST_INTERVAL - (asyncio.get_running_loop().time() - last_request)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    response = await client.get(url)
                except httpx.HTTPError:
                    response = None
                last_request = asyncio.get_running_loop().time()
            if response is not None and response.status_code == 200:
                return response.json()
            if response is not None and response.status_code not in (429, 500, 502, 503, 504):
                return None
            await asyncio.sleep(min(5.0 * (attempt + 1), 30.0))
        return None

    async def walk(client: httpx.AsyncClient, path: str, breadcrumb: list[str]) -> None:
        nonlocal folders
        data = await fetch(client, f"{HAGSTOFA_API}{path}/" if path else HAGSTOFA_API)
        if data is None:
            failed.append(path)
            return
        folders += 1
        if progress:
            progress(folders, len(tables), path)
        for item in data:
            item_id = item.get("id") or item["dbid"]
            full = f"{path}/{item_id}" if path else item_id
            title = item.get("text", "")
            if item.get("type", "l") == "l":
                await walk(client, full, breadcrumb + [title])
            else:
                tables.append(CatalogTable(path=full, title=title, updated=item.get("updated"), breadcrumb=breadcrumb))

    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        await walk(client, "", [])
    if failed:
        raise RuntimeError(f"{len(failed)} folder(s) could not be read (e.g. {failed[0]}); snapshot not saved")
    return CatalogSnapshot(crawled_at=_utc_now(), folders_crawled=folders, tables=tables)


def _write_snapshot(snapshot: CatalogSnapshot, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(snapshot.model_dump_json(), encoding="utf-8")
    tmp.replace(path)


def _read_snapshot(path: Path) -> CatalogSnapshot | None:
    try:
        return CatalogSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_snapshot: CatalogSnapshot | None = None
_refresh_task: asyncio.Task | None = None


def load_snapshot() -> CatalogSnapshot | None:
    global _snapshot
    if _snapshot is None:
        candidates = [s for s in (_read_snapshot(cache_path()), _read_snapshot(SEED_PATH)) if s]
        _snapshot = max(candidates, key=lambda s: s.crawled_at, default=None)
    return _snapshot


def snapshot_age_days(snapshot: CatalogSnapshot) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(snapshot.crawled_at)).total_seconds() / 86400


def refreshing() -> bool:
    return _refresh_task is not None and not _refresh_task.done()


async def _refresh() -> None:
    global _snapshot
    try:
        snapshot = await crawl_catalog()
    except Exception:
        return
    _snapshot = snapshot
    try:
        _write_snapshot(snapshot, cache_path())
    except OSError:
        pass


def ensure_fresh() -> None:
    """Start a background re-crawl if the snapshot is missing or stale (at most one at a time)."""
    global _refresh_task
    snapshot = load_snapshot()
    if refreshing():
        return
    if snapshot is None or snapshot_age_days(snapshot) > CATALOG_MAX_AGE_DAYS:
        _refresh_task = asyncio.get_running_loop().create_task(_refresh())


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

_FOLD = str.maketrans({"þ": "th", "ð": "d", "æ": "ae", "ø": "o"})

# English keywords -> Icelandic terms as they appear in Hagstofa's folder/table titles.
EN_TO_IS = {
    # Icelandic words that are not themselves in table titles (Hagstofa publishes the index, not "verðbólga").
    "verðbólga": ["vísitala neysluverðs"],
    "verðbólgu": ["vísitala neysluverðs"],
    "atvinnuleysi": ["vinnumarkaðsrannsókn", "atvinnuleysi"],
    "inflation": ["verðbólga", "vísitala neysluverðs", "verðlag"],
    "cpi": ["vísitala neysluverðs"],
    "consumer price": ["vísitala neysluverðs"],
    "prices": ["verðlag", "vísitala"],
    "gdp": ["landsframleiðsla", "þjóðhagsreikningar"],
    "national accounts": ["þjóðhagsreikningar"],
    "unemployment": ["atvinnuleysi", "vinnumarkaður"],
    "employment": ["starfandi", "vinnumarkaður"],
    "labour": ["vinnumarkaður"],
    "labor": ["vinnumarkaður"],
    "wages": ["laun"],
    "salaries": ["laun"],
    "income": ["tekjur"],
    "population": ["mannfjöldi"],
    "births": ["fæðingar"],
    "deaths": ["dánir"],
    "immigration": ["aðfluttir", "búferlaflutningar"],
    "migration": ["búferlaflutningar"],
    "housing": ["húsnæði", "fasteignir"],
    "house prices": ["fasteignaverð", "húsnæðisverð"],
    "tourism": ["ferðaþjónusta", "ferðamenn", "gistinætur"],
    "tourists": ["ferðamenn"],
    "fish": ["sjávarútvegur", "afli"],
    "fisheries": ["sjávarútvegur", "afli"],
    "exports": ["útflutningur", "utanríkisverslun"],
    "imports": ["innflutningur", "utanríkisverslun"],
    "trade": ["utanríkisverslun"],
    "energy": ["orka"],
    "emissions": ["losun"],
    "education": ["skólamál", "menntun"],
    "election": ["kosningar"],
    "elections": ["kosningar"],
    "vehicles": ["ökutæki", "bifreiðar"],
    "cars": ["bifreiðar", "ökutæki"],
    "health": ["heilbrigðismál"],
    "crime": ["afbrot", "sakamál"],
    "agriculture": ["landbúnaður"],
    "construction": ["byggingarstarfsemi", "byggingarvísitala"],
    "exchange rate": ["gengi"],
    "interest": ["vextir"],
    "government": ["opinber fjármál", "ríkissjóður"],
    "public finance": ["opinber fjármál"],
}


def fold(text: str) -> str:
    text = text.lower().translate(_FOLD)
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(text))


def _word_matches(token: str, word: str) -> bool:
    # Prefix match tolerates Icelandic inflection (verðbólga / verðbólgu / verðbólgunnar).
    if word.startswith(token):
        return True
    return len(token) >= 6 and word.startswith(token[:-2])


def _expand_query(query: str) -> list[list[str]]:
    """Return alternative token lists: the query itself plus each Icelandic translation found."""
    variants = [_tokens(query)]
    lowered = query.lower()
    for en, is_terms in EN_TO_IS.items():
        if re.search(rf"\b{re.escape(en)}\b", lowered):
            variants.extend(_tokens(term) for term in is_terms)
    return [v for v in variants if v]


class CatalogSearchHit(BaseModel):
    path: str
    title: str
    folder: str
    updated: str | None = None
    score: float


def search_catalog(snapshot: CatalogSnapshot, query: str, limit: int = 15) -> list[CatalogSearchHit]:
    variants = _expand_query(query)
    if not variants:
        return []
    hits: list[CatalogSearchHit] = []
    for table in snapshot.tables:
        title_words = _tokens(table.title)
        crumb_words = [w for crumb in table.breadcrumb for w in _tokens(crumb)]
        id_words = _tokens(table.path.replace("/", " ").replace("_", " "))
        best = 0.0
        for variant in variants:
            score = 0.0
            for token in variant:
                if any(_word_matches(token, w) for w in title_words):
                    score += 3
                elif any(_word_matches(token, w) for w in crumb_words):
                    score += 2
                elif any(_word_matches(token, w) for w in id_words):
                    score += 1
                else:
                    score = 0.0
                    break  # every token of a variant must match somewhere
            if score:
                # Prefer the variant that matched the most/strongest tokens; favour the
                # user's own wording (variant 0) over synonym expansions.
                score = score / len(variant) + len(variant) * 0.1 + (0.5 if variant is variants[0] else 0)
            best = max(best, score)
        if best:
            hits.append(
                CatalogSearchHit(
                    path=table.path,
                    title=table.title,
                    folder=" > ".join(table.breadcrumb),
                    updated=table.updated,
                    score=round(best, 2),
                )
            )
    # Ties on relevance: live series before frozen "eldra efni" (older material) ones, then the most
    # recently updated, then the shorter (more general) title. Stable sorts, last key is primary.
    hits.sort(key=lambda h: len(h.title))
    hits.sort(key=lambda h: h.updated or "", reverse=True)
    hits.sort(key=lambda h: "eldra" in fold(h.path) or "eldra" in fold(h.folder))
    hits.sort(key=lambda h: -h.score)
    return hits[:limit]


def main() -> None:
    """CLI: rebuild the packaged seed snapshot (`iceland-context-hagstofa-crawl [output-path]`)."""
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else SEED_PATH

    def progress(folders: int, tables: int, path: str) -> None:
        print(f"\r{folders} folders, {tables} tables  {path[:60]:<60}", end="", file=sys.stderr, flush=True)

    snapshot = asyncio.run(crawl_catalog(progress))
    print(file=sys.stderr)
    _write_snapshot(snapshot, target)
    print(f"Wrote {len(snapshot.tables)} tables from {snapshot.folders_crawled} folders to {target}")


if __name__ == "__main__":
    main()
