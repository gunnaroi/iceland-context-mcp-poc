# Hagstofa API notes (verified against the live endpoints)

- **PX-Web v1 only.** `px.hagstofa.is/pxis/api/v1/is/` (Icelandic) and `px.hagstofa.is/pxen/api/v1/en/` (English). `/api/v2/`
  does not exist here, so there is no server-side search; the tools search a cached crawl of the folder tree
  (`scripts/iceland_context_mcp/hagstofa_catalog.json`, 2,102 tables, 1,777 with English titles). It refreshes itself in
  the background when older than 14 days (cache in `~/.cache/iceland-context-mcp`, override with
  `ICELAND_MCP_CACHE_DIR`). To rebuild the seed by hand, run `python -m iceland_context_mcp.hagstofa_catalog` from
  `scripts/` (about 3 minutes, sequential on purpose).
- **Bare errors.** Any wrong path, folder-instead-of-table, or bad filter value returns HTTP 400 with no body. The tools
  check paths and filters against the table's metadata first so you get a readable message instead.
- **Limits** (published at `<base>?config`): 100,000 cells per query, 5,000 selected values, and a rate limit
  (300 calls / 10 s for Icelandic, 100 / 10 s for English). Concurrent bursts get HTTP 429 — go one request at a time.
- **Encoding.** CSV responses sometimes declare `charset=Windows-1252` while the body is UTF-8 with a BOM; the tools
  detect the BOM first. If you ever fetch raw CSV yourself, do the same.
- **Time variable.** Periods look like `2026M08` (month), `2025K3` (quarter), `2024` (year). `last_n_periods` uses
  PX-Web's `top` filter on the table's time variable.
- **Two languages, two vocabularies.** Variable codes, value codes and labels differ between `is` and `en` — never mix.
- **Frozen material.** Folders/tables with `eldra`/`eldraefni` in the path are historical editions no longer updated.
- **Root listing** uses `{dbid, text}`; deeper levels use `{id, type: l|t, text, updated}` (`l` folder, `t` table).
- **Units.** Read the table title and the measure variable: values are frequently in thousands of ISK, or an index
  against a base year; CPI monthly change and annual change are separate items of the same variable.
