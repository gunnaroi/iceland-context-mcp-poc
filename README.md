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
- `get_iceland_eea_status(celex)` — public EES-gagnagrunnur retrieval;
- `get_efta_eea_factsheet(celex)` — public EFTA EEA-Lex retrieval;
- `trace_eea_public_context(celex)` — combines the two EEA evidence sources;
- MCP server instructions/resources that tell clients how to distinguish legal authority and status, including the reglugerð↔lög subordination and the bill-vs-enacted-law distinction.

No protected island.is/X-Road data, authenticated portals, write tools, or internal documents are used.

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

Implemented so far: Alþingi open parliamentary XML (`get_bill`) and the reglugerð register with law-basis extraction
(`get_regulation`, `search_regulations`). Remaining, roughly in order:

1. Point-in-time Lagasafn text (`as_of` on `get_law`) by indexing the per-session archive instead of only the latest snapshot;
2. Stjórnartíðindi A-deild retrieval/structured promulgation metadata;
3. Samráðsgátt;
4. Court rulings — `island.is/domar` now unifies all three court levels behind a public GraphQL API (`island.is/api/graphql`, `webVerdicts`/`webVerdictById`, with a `laws` filter), which is simpler than the three separate court sites this repo originally assumed; full ruling text often comes back as a base64 PDF rather than plain text, so a PDF-extraction step is still needed. Independently re-check `robots.txt` before building against it — the earlier per-court `/domar*` disallow may or may not carry over post-migration;
5. EUR-Lex/CELLAR as the EU machine source.

Keep the MCP tool surface stable while swapping brittle HTML adapters for supported source contracts.
