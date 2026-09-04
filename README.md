# Iceland Trusted Context MCP — public-only proof of concept

This is a deliberately narrow MCP proof of concept for giving AI systems **public Icelandic legal and EEA context without any internal government access**.

It implements the principle that the MCP server is a **read-only routing/retrieval layer, not the system of record**. Every live retrieval carries publisher/source provenance and explicit legal-status warnings.

## PoC scope

The first version exposes:

- a small **source registry** with authority/use classifications;
- `get_law(year, number)` — live current consolidated Lagasafn retrieval;
- `search_laws(query)` — optional local full-text discovery index built from the latest public Alþingi SGML snapshot;
- `get_regulation(number, year, view)` — official reglugerð register (current or as-originally-published text), with amendment history and a best-effort extraction of the regulation's stated legal basis (enabling law);
- `search_regulations(query)` — free-text search over the regulation register;
- `get_bill(malnr, thing, malsflokkur)` — Alþingi parliamentary matter (bill/resolution/question) with status, subject categories and its document trail (stjórnarfrumvarp/nefndarálit/breytingartillaga/...);
- `get_bill_document(thing, document_number)` — full text of one þingskjal from that trail (HTML normally, falling back to the document's own PDF when there's no inline text — e.g. fjárlög, the state budget, which is itself legislation and PDF-only; its enacted-law PDF is the canonical source for exact appropriation figures, not a ministry CSV mirror);
- `search_court_rulings(query, court, date_from, date_to, law_citation)` / `get_court_ruling(id)` — court rulings (héraðsdómur/Landsréttur/Hæstiréttur) via the unified island.is verdict register, each carrying a court-level authority_class (C1/C2/C3) reflecting precedential weight; `law_citation` filters by a curated whole-law citation tag;
- `get_eur_lex_act(celex, language)` — official EU act text and metadata via the public CELLAR SPARQL + REST endpoints (no API key) — the EU-law side of the chain;
- `get_iceland_eea_status(celex)` — public EES-gagnagrunnur retrieval;
- `get_efta_eea_factsheet(celex)` — public EFTA EEA-Lex retrieval;
- `trace_eea_public_context(celex)` — combines the two EEA evidence sources;
- MCP server instructions/resources that tell clients how to distinguish legal authority and status, including the reglugerð↔lög subordination and the bill-vs-enacted-law distinction.

No protected island.is/X-Road data, authenticated portals, write tools, or internal documents are used.

## Open-data tools and reference resources beyond this PoC's legal/EEA scope

`context://iceland-data/index` and `context://iceland-data/skill/{name}` expose all 56 `SKILL.md` docs from
[jokull/icelandic-data](https://github.com/jokull/icelandic-data) (MIT-licensed, vendored with attribution —
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)) as **reference documentation only** — not retrieved
live, no provenance/authority-class, this project makes no claim about their accuracy or currency. This is a
deliberate scope decision (see conversation history), not scope creep by accident.

On top of that documentation, `open_data.py` adds **live** tools for every one of those 56 sources that has
a genuine public API/WFS/bulk-download endpoint — no HTML scraping, no reverse-engineered Power BI/Tableau
dashboard, no Playwright/JS rendering. Implemented so far (`context://iceland-data/registry` has full notes):

| Tool | Covers |
|---|---|
| `get_geodata(source_key, layer, ...)` | `umferd` (traffic counters), `fiskistofa` (fishing closures), `ust-gis` (contaminated land), `lmi` (national topographic/admin geodata), `natt` (Náttúrufræðistofnun vector layers) — one generic WFS client for all five |
| `get_hagstofa_table(table_path, filters)` | `hagstofan` (any PX-Web table) and `income-distribution` (TEK01001 is just another table path) |
| `get_vehicle(search)` | `car` — exact plate/VIN lookup |
| `get_eurostat_series(dataset, filters)` | `eurostat` — EU/euro-area comparison series |
| `get_weather_observations` / `get_earthquakes` | `vedur` |
| `get_air_quality(date, station_local_id)` | `loftgaedi` |
| `get_bond(orderbook_id)` | `lanamal` — RIKB/RIKS government bond yields |
| `get_rikisreikningur_summary` / `get_rikisreikningur_malefni` | `rikisreikningur` — state accounts actuals, government-wide and by policy area |
| `search_government_invoices` / `search_invoice_orgs` | `opnirreikningar` — paid central-government invoices |
| `search_planning_minutes` / `get_nearby_planning_cases` | `skipulagsmal` — Planitor planning/building-permit data (Reykjavík/Hafnarfjörður/Árborg only) |
| `get_sdg_indicator(code, lang)` | `heimsmarkmid` — Iceland's 137 UN SDG indicators |
| `search_tenders` | `tenders` — TED EU procurement notices (EEA-threshold only; OCDS bulk history not covered) |
| `search_eea_datasets` | `eea-sdi` — EEA geospatial dataset catalogue search (not the underlying data) |
| `get_fx_rate(date, base, symbols)` | `gengi`'s historical side — ECB reference rates via frankfurter.dev |

Like the reference resources, these carry no authority-class — this data isn't legal in nature. Unlike the
reference resources, they're live retrieval, same as this PoC's own core tools.

### Remaining sources — tiered by feasibility, not yet built

**Not pursued, wrong shape for this tool surface:** `lmi-hrl` and most of `natt` (the national habitat map in
particular) are large-raster WCS/GeoTIFF coverages — hundreds of MB to multi-GB pixel data, not something an
LLM-facing tool should hand back. `natt`'s GeoServer does expose genuine vector WFS layers though (federated
from LMI/Hagstofa/others), so it's covered by `get_geodata` for those. `gengi`'s *current* Borgun card-rate
side was left out too — the skill doc names no concrete endpoint for it, only prose; the ECB historical side
via frankfurter.dev is what's implemented.

`fjarlog` (the skill's own CSV mirror on stjornarradid.is) turned out not to be worth pursuing: that site is
Blazor Server-rendered (the download link isn't in the plain HTML, and the filename carries a changing version
suffix with no discoverable stable alias — confirmed live, all attempted paths either need a JS-rendered
session or 302 ambiguously). Since fjárlög is itself legislation, `get_bill_document` reading its enacted-law
PDF directly off althingi.is is the better source anyway — verified live, extracting real appropriation
figures by málaflokkur (e.g. "Menning, listir, íþrótta- og æskulýðsmál" → 3.445,4 m.kr. in the 2026 budget).

**PDF-based, not attempted yet (own tier — fetching+parsing a public PDF isn't scraping, but locating some of
these PDFs may need it):** `financials`, `skatturinn`, `nasdaq`, `insurance`, `annual-report-cache`.

**Excluded per explicit scope decision — requires scraping, Power BI/Tableau reverse-engineering, or
Playwright/JS rendering:** `byggdastofnun`, `co2`, `farsaeld-barna`, `ferdamalastofa`, `hms`, `landlaeknir`,
`maelabord-landbunadarins`, `maskina`, `samgongustofa`, `sedlabanki` (its SDMX parts could be revisited),
`skodanakannanir`, `tekjusagan`, `vernd`, `vinnumalastofnun`.

**Not real independent sources (methodology docs or derivative of another skill already covered):**
`new-data-source`, `pdf-parsing`, `liteparse`, `powerbi`, `kortagerd`, `sectoral-balances`, `iceaddr` (an
offline bundled dataset, not a live source), `laun` (a calculator, not a data retrieval).

`hafogvatn`'s "embedded JSON inside static HTML" sits right on the scraping/API line and wasn't attempted
this round — worth a closer look before deciding either way.

## Why this is a good first PoC

It demonstrates the hard part of the idea with publicly observable material:

1. **authority-aware routing** rather than generic web search;
2. **provenance in every result**;
3. a concrete **EU → EEA → Iceland** context chain;
4. a clean separation between **discovery** and **authoritative retrieval**;
5. an MCP interface that can later sit on top of better supported feeds without changing the tool contract.

## Requirements

- Python 3.10+
- `uv` recommended
- network access to the allowlisted official public sources

## Install

```bash
uv sync --extra dev
```

Or with pip:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Test the MCP server locally

The official MCP Python SDK v2 supports the development Inspector:

```bash
uv run mcp dev src/iceland_context_mcp/server.py
```

The server also runs over stdio:

```bash
uv run iceland-context-mcp
```

## Build the optional Lagasafn discovery index

```bash
uv run iceland-context-bootstrap
```

The bootstrapper discovers the latest public SGML ZIP from Alþingi, downloads it, extracts searchable text and creates:

```text
data/lagasafn.sqlite3
```

This index is **not treated as current legal authority**. Search results are discovery hints; use `get_law` to retrieve the current official page before relying on text.

## Run a local Streamable HTTP endpoint

```bash
MCP_TRANSPORT=streamable-http MCP_HOST=127.0.0.1 MCP_PORT=8000 uv run iceland-context-mcp
```

The MCP endpoint will be available at:

```text
http://127.0.0.1:8000/mcp
```

For a public deployment, do not simply expose the development binding. Put the ASGI/MCP service behind TLS, configure MCP transport security/allowed hosts, add rate limits and request logging, and keep the source allowlist fixed.

## Recommended first demonstrations

### 1. Direct law retrieval

Ask an MCP client to retrieve a known law by year and number. Confirm that it returns the live Alþingi URL and the consolidated-text warning.

### 2. Law discovery then verification

Build the Lagasafn index, search for a topic, then have the client call `get_law` for the chosen result rather than treating the index snippet as authoritative.

### 3. EEA status

Try a CELEX number such as `32016R0679`. The model should use the Icelandic EES source and EFTA source, and it should **not** claim Icelandic applicability solely because the EU act exists.

## Security properties in this PoC

- fixed official-source allowlist; no arbitrary URL fetch tool;
- strict CELEX and law-number URL construction (reduces SSRF risk);
- response-size and timeout limits;
- retrieved page content is labelled as data and the server instructions say it must never be treated as instructions;
- read-only tools only;
- no credentials or personal/private data.

## Known PoC limitations

- The EES and EFTA adapters currently parse public HTML. This is acceptable for a demonstrator but should be replaced by supported feeds/APIs if/when available.
- Lagasafn SGML parsing is intentionally generic and must be validated against representative documents/annexes before any production use.
- `get_regulation`'s `law_basis` field is a regex extraction of the regulation's own "heimild"/"lagastoð" clause, not a verified structured field — it can miss a citation phrased unusually, or (rarely) pick up a spurious `nr. N/YYYY` match near an unrelated use of "heimild" in the text.
- Point-in-time law text (Lagasafn `as_of`) is not yet implemented, even though Alþingi's per-session Lagasafn archive (`lagasafn/zip/{session}/allt_sgml.zip`, sessions 119+) makes it feasible without any new source.
- The PoC does not yet reconstruct amendment graphs across laws, Stjórnartíðindi A-deild structure, court citations, or Samráðsgátt outcome links.
- The PoC does not make legal determinations. It returns evidence and status context for an AI/client to reason over.

## Suggested next increment

Implemented so far: Alþingi open parliamentary XML (`get_bill`), the reglugerð register with law-basis extraction
(`get_regulation`, `search_regulations`), and unified court rulings (`search_court_rulings`, `get_court_ruling`).
Remaining, roughly in order:

1. Point-in-time Lagasafn text (`as_of` on `get_law`) by indexing the per-session archive instead of only the latest snapshot;
2. Stjórnartíðindi A-deild retrieval/structured promulgation metadata;
3. Samráðsgátt;
4. **Implemented**: `search_court_rulings(law_citation=...)` filters rulings by a whole-law citation tag (format
   `"NNN/YYYY"`). My first attempt at this guessed wrong string formats for the underlying `webVerdicts`
   `laws` field and got inconsistent results (1–3 hits for heavily-litigated laws, or a ~31k near-unfiltered
   fallback) — driving the real island.is search UI in a browser and capturing its network requests revealed
   the correct format is a dotted `"YYYY.NNN"` tag, which the frontend itself converts from natural
   `"NNN/YYYY"` input. At that correct whole-law granularity, results are consistently plausible (e.g.
   `"91/1991"` → 65 genuine hits, verified by reading the actual citation text). Article-level filtering
   (e.g. `"1991.91.25.1"`) is explicitly marked "fleiri möguleikar eru í vinnslu" (more options in
   development) on island.is's own UI and was confirmed unreliable — not exposed by this tool.
   There is still no structured **regulation**→judgment link (only law→judgment via `law_citation`, and
   regulation→law via `get_regulation`'s `law_basis`) — extending `_extract_law_basis`'s pattern to scan a
   ruling's full text for every `laga/reglugerðar nr. X/Y` mention remains the open path for that gap;
5. **Implemented**: `get_eur_lex_act(celex, language)` retrieves official EU act text and metadata directly
   from CELLAR (metadata via its public SPARQL endpoint using the CDM ontology, text via its
   content-negotiated REST endpoint) — the same backend eur-lex.europa.eu itself runs on, no API key. Modeled
   on the query patterns from two actively-maintained open-source EUR-Lex MCP servers
   ([cyanheads/eur-lex-mcp-server](https://github.com/cyanheads/eur-lex-mcp-server),
   [Honeyfield-Org/eurlex-mcp-server](https://github.com/Honeyfield-Org/eurlex-mcp-server), both Cellar-based
   with no auth) rather than a third one ([scimorph/eur-lex-mcp](https://github.com/scimorph/eur-lex-mcp))
   that wraps EUR-Lex's legacy SOAP webservice and requires registered credentials — disqualified by this
   PoC's no-credentials scope. One real bug caught before shipping: the REST fetch needs an explicit
   `Accept: application/xhtml+xml, text/html` header — without it, CELLAR silently serves a much shorter
   metadata-only representation instead of the actual act text, no error at all (first attempt returned 742
   characters of EuroVoc keywords for a regulation whose real text is 372K+ characters; caught by checking the
   output looked implausibly short, not by any error signal).

Keep the MCP tool surface stable while swapping brittle HTML adapters for supported source contracts.

### Known upstream quirks (verified against the live endpoints, not assumptions)

- `search_regulations`: the reglugerð API's `perPage` parameter is silently ignored server-side (always returns a fixed page size); `limit` is enforced client-side instead.
- `search_court_rulings`: the `court` filter is confirmed reliable only for `"Hæstiréttur"` — `"Landsréttur"` or a héraðsdómur name silently returns zero results even though those exact strings appear in the returned data. Filter by court client-side for anything but Hæstiréttur.
- `get_court_ruling`: full text is structured `richText` for some rulings (mainly recent Hæstiréttur) and a PDF (extracted via `pdfplumber`) for others — check `text_source` on the result.
