---
name: iceland-statistics
description: Look up official Icelandic statistics straight from Hagstofa Íslands (Statistics Iceland) and compare them with Eurostat, without an account or API key. Covers inflation and price indices (verðbólga, vísitala neysluverðs), wages and income (laun, tekjur), GDP and national accounts (landsframleiðsla), population and migration (mannfjöldi, aðfluttir), housing, labour market and unemployment (atvinnuleysi), tourism and Keflavík passengers (ferðamenn), fisheries catch (afli), foreign trade, energy, education, elections, the UN SDG indicators for Iceland (heimsmarkmið), and euro-area/EU comparisons for Iceland (geo=IS). Use this whenever a question needs an actual Icelandic statistic, time series or table — "how has X developed in Iceland", "Hagstofa tölur um ...", "compare Iceland with the euro area" — even if the user doesn't mention Hagstofa or an API. Numbers must come from retrieved data, never from memory.
---

# Icelandic statistics

Hagstofa publishes ~2,100 tables through a PX-Web API. The data is excellent but the interface is unforgiving: folder
names are abbreviations (`Efnahagur/visitolur/1_vnv/1_vnv/VIS01000.px`), there is no search endpoint, and a guessed
path or filter value just gets a bare `400`. This skill bundles a cached index of Hagstofa's whole catalogue and a small
CLI that turns "find, inspect, fetch" into three calls, with errors that say what to do next. Use it instead of web
search or memory: index values move every month, and a stale or misremembered figure attributed to Hagstofa is worse
than no figure.

## Running it

```bash
uv run scripts/iceland_stats.py list                    # the 9 tools
uv run scripts/iceland_stats.py describe get_hagstofa_table
uv run scripts/iceland_stats.py call search_hagstofa_tables query="verðbólga"
uv run scripts/iceland_stats.py call get_hagstofa_table \
  table_path=Efnahagur/visitolur/1_vnv/1_vnv/VIS01000.px \
  filters='{"Vísitala": ["CPI"], "Liður": ["Ársbreyting, %"]}' last_n_periods=24
```

Arguments are `key=value` pairs (values that parse as JSON are JSON), or one JSON object: `call TOOL '{"a": 1}'`,
`call TOOL -` (stdin) or `call TOOL @args.json`. Paths are relative to this skill's folder. `uv` installs the three
small dependencies on first run; without it, `pip install httpx pydantic lxml` and use `python scripts/iceland_stats.py`.
Output is JSON on stdout. Errors go to stderr with exit status 2 and are written as instructions (which tool to call,
which values are valid) — read them rather than retrying with another guess.
Hagstofa rate-limits per client address (HTTP 429, released only after a quiet spell): run requests one after
another, never in parallel; the tools already wait and retry, and if they still report 429 stop calling for about
five minutes instead of hammering, which keeps the block in place. The sandbox needs outbound HTTPS to
`px.hagstofa.is`, `ec.europa.eu` and `hagstofan.github.io`; if it is blocked, say so instead of falling back to
remembered numbers.

## The Hagstofa workflow

1. **Find the table.** `search_hagstofa_tables query="..."` — Icelandic or English (hits show `title_en` when an English
   edition exists), inflected forms match, and common English terms are translated. Several tables often cover a topic;
   prefer recent `updated` dates over "eldra efni" (older, frozen) ones and read the titles for base year or breakdown.
   Hits marked `partial` matched only some of your words (e.g. the Keflavík passenger tables when you add
   "departures", a word their titles don't use) — read the title and definition before trusting them. No hit at
   all? Try fewer or different words, or `browse_hagstofa_tables` folder by folder.
2. **Inspect it.** `get_hagstofa_table_info table_path=...` lists the variables, how many values each has, sample values,
   and the time range (first 2 and last 4 periods). To see one variable's valid values:
   `variable=CODE query=substring`. Look at this before filtering — variable codes differ from table to table.
3. **Fetch it.** `get_hagstofa_table table_path=... filters={...} last_n_periods=N`. `filters` maps variable code →
   list of values, given as codes or exact labels; `"*"` selects all. Omitted variables default to all values (or are
   summed away when the table allows it). Use `last_n_periods` for the time variable rather than filtering it.
   Hagstofa refuses selections over 100,000 cells or 5,000 selected values — the error names which variables to narrow.

`language=en` on the info/fetch tools gives English variable names and labels for tables that have an English edition
(~85%). Variable codes are *different* in each language (`Vísitala` vs `Index`), so pick one language per query.

The API returns bare numbers: **no provisional flags and no footnotes**. The newest periods of many series are
provisional and get revised, and a table title can be looser than the question ("passengers through the airport by
citizenship" is not strictly "foreign visitors departing"). Tell the user which definition the table actually
uses, note that the latest periods may be revised, and point to hagstofa.is for footnotes when they matter.

Check units before interpreting: many tables are in thousands of ISK or an index against a base year; some carry
several measures in one variable (mean vs median, monthly vs annual change). Missing values appear as `.` or `..` —
keep them as gaps, not zeros. More gotchas are in `references/hagstofa-notes.md`.

Worked example — Icelandic CPI: the monthly national index (base 1988=100) is
`Efnahagur/visitolur/1_vnv/1_vnv/VIS01000.px`; the annual change is the `Ársbreyting, %` item of variable `Liður`,
with `Vísitala=CPI` (`CPILH` is the index excluding housing).

## Comparing with Eurostat

`search_eurostat_datasets query=...` → `get_eurostat_dataset_info dataset=...` (dimensions and valid codes; use
`get_eurostat_dimension_values dataset=... dimension=geo query=ice` for one dimension) →
`get_eurostat_series dataset=... filters='{"geo": "IS+EA20", ...}' last_n_periods=N`. Iceland is `geo=IS`; several
values are joined with `+`. Unknown filter values are rejected up front (Eurostat itself would silently treat them as
"no filter" and then fail with "extraction too big"). Check `data_end` in the search results: older editions freeze
(`prc_hicp_midx` and `prc_hicp_manr` stop in 2025; their successor is `prc_hicp_minr`, whose classification
dimension is `coicop18`, not `coicop`). Observations carry Eurostat's flag (`p` provisional,
`e` estimated, `b` break) and `value: null` for missing points — keep those visible.

Worked example — HICP annual rate of change, Iceland vs euro area, last 24 months: `get_eurostat_series
dataset=prc_hicp_minr filters='{"geo":"IS+EA","coicop18":"TOTAL","unit":"RCH_A"}' last_n_periods=24` (`EA` is
Eurostat's changing-composition euro area; `EA20` is a fixed aggregate).

**Comparability matters.** Icelandic national CPI (`VIS01000`) includes owner-occupied housing costs; the HICP that
Eurostat publishes for Iceland does not. For "Iceland vs the euro area" use HICP for both (Eurostat `geo=IS` against
`geo=EA`/`EA20`) and show the national CPI separately if useful. Say which measure each number is. Hagstofa's CPI for
the latest month usually appears before Eurostat's, so the last month may exist for only one series.

## SDG indicators

`get_sdg_indicator code=1-1-1 lang=en` returns the full series of one of Iceland's 137 UN SDG indicators (from
Hagstofa's open-sdg platform). A year can repeat when the series is disaggregated; that is normal, not duplicate data.

## Presenting results

- **Only fetched numbers.** State a figure only if a tool returned it in this session; if a call failed, say so — a
  gap is an honest answer. Label your own arithmetic (differences, growth rates, averages) as yours and show inputs.
- **Cite the source** for each series: table path or Eurostat dataset code, the measure and unit, and the retrieval
  date (`retrieved_at`), plus `source_url`.
- **Answer first**, then the table of values, then caveats (definition differences, base year, provisional flags,
  series breaks, months missing from one source).
- Results are capped at 500 rows: `total_rows` and `truncated` say if the tool cut the output — narrow with filters or
  `last_n_periods` rather than presenting a partial series as complete.

## What this skill does not do

It is statistics only: no legislation, court rulings, EEA/EU legal status or registry lookups. Other Nordic statistical
offices (SSB, SCB, StatFin) run the same PX-Web software but are not wired in here.
