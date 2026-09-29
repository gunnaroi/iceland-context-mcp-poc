# Tool reference

Generated from the MCP server's tool descriptions (`skill/build.py`); `describe TOOL` prints the same text.

## search_hagstofa_tables

Keyword-search Hagstofa Íslands' ~2,100 statistical tables (the fastest way to find a table path).

    START HERE for any Icelandic statistics question. Searches table titles and their folder names in a
    locally cached index of Hagstofa's whole catalogue (PX-Web itself has no search). Icelandic terms work
    best and inflected forms match (verðbólga / verðbólgu); common English terms (inflation, GDP,
    unemployment, population, wages, housing, tourism, ...) are translated automatically. Every word must
    match. Returns table `path`s to pass unmodified to get_hagstofa_table_info (to see variables/valid
    values) and then get_hagstofa_table. Prefer results whose `updated` date is recent; older
    "eldra efni" tables are frozen historical series.

Parameters:

- `query: string (required)`
- `limit: integer (optional, default 15)`

## get_hagstofa_table_info

Inspect one Hagstofa table before querying it: its variables, how many values each has, and sample values.

    Use the `path` from search_hagstofa_tables/browse_hagstofa_tables. Without `variable` you get every
    variable with a few sample values (for the time variable: first 2 and last 4 periods, so you can see how
    recent the data is). To see the valid codes for one variable, pass `variable` (its code) and optionally
    `query` to filter values by substring — e.g. variable='Sveitarfélag', query='Reykjav'. `total_cells` is
    the size of an unfiltered query; Hagstofa rejects selections over 100,000 cells (or 5,000 selected values).
    `language='en'` returns English variable codes/labels for tables that have an English edition (search hits
    with `title_en`); note the variable codes differ between languages, so use one language throughout.

Parameters:

- `table_path: string (required)`
- `variable: string (optional, default null)`
- `query: string (optional, default null)`
- `limit: integer (optional, default 50)`
- `language: string (optional, default "is")`

## get_hagstofa_table

Fetch data from a Hagstofa Íslands (Statistics Iceland) PX-Web table.

    Workflow: search_hagstofa_tables (find the table) -> get_hagstofa_table_info (see variables and valid
    values) -> this tool. table_path must be the exact path returned by search/browse — never guessed or
    shortened (a bare filename like 'VIS01000.px' fails).

    `filters` maps a variable code to a list of values, given as codes or as exact labels; '*' selects all
    values, e.g. {"Vísitala": ["CPI"], "Liður": ["change_A"]}. Variables you omit default to all values (or
    are summed away when the table allows it). `last_n_periods` returns only the latest N periods of the
    table's time variable — use it instead of filtering time, and to keep large tables under Hagstofa's
    100,000-cell limit. Errors list the valid variables/values. Values are as published — check the
    table's own units/scale (e.g. thousands of ISK). `language='en'` gives English column names/labels (and
    English variable codes) for tables with an English edition. Verified example: CPI (vísitala neysluverðs, monthly since
    1988) is 'Efnahagur/visitolur/1_vnv/1_vnv/VIS01000.px'. Unrelated to this PoC's legal/EEA tools; no
    authority-class/provenance.

Parameters:

- `table_path: string (required)`
- `filters: object (optional, default null)`
- `last_n_periods: integer (optional, default null)`
- `language: string (optional, default "is")`

## browse_hagstofa_tables

Browse Hagstofa Íslands' PX-Web folder tree one level at a time (alternative to search_hagstofa_tables).

    Prefer search_hagstofa_tables when you know what you are looking for; browse when exploring what
    exists. Call with no path for the top-level subject areas, then pass a returned folder's `full_path`
    back in to descend one level — do not skip levels or invent segments — until entry_type='table'
    entries appear. Pass a table's `full_path` verbatim to get_hagstofa_table_info/get_hagstofa_table.
    Unrelated to this PoC's legal/EEA tools.

Parameters:

- `path: string (optional, default "")`
- `language: string (optional, default "is")`

## search_eurostat_datasets

Keyword-search Eurostat's ~8,900 datasets by title, code or theme (e.g. 'hicp inflation', 'unemployment').

    START HERE to find an Eurostat dataset code — codes like 'prc_hicp_minr' can't be guessed. Results show
    each dataset's data_start/data_end: prefer one whose data_end is recent (older editions are frozen —
    e.g. prc_hicp_midx stops at 2025-12, its successor is prc_hicp_minr). Next call
    get_eurostat_dataset_info, then get_eurostat_series. Iceland is geo=IS in most datasets.

Parameters:

- `query: string (required)`
- `limit: integer (optional, default 15)`

## get_eurostat_dataset_info

List an Eurostat dataset's dimensions (in order), how many codes each has, and sample codes.

    Use before get_eurostat_series to learn the dimension names and valid codes for `filters`. For the
    full code list of one dimension (e.g. every geo or COICOP code), use get_eurostat_dimension_values.

Parameters:

- `dataset: string (required)`

## get_eurostat_dimension_values

List the valid codes and labels of one dimension of an Eurostat dataset, optionally filtered by `query`.

    e.g. dataset='prc_hicp_minr', dimension='geo', query='ice' -> IS (Iceland). Only codes actually used by
    this dataset are listed.

Parameters:

- `dataset: string (required)`
- `dimension: string (required)`
- `query: string (optional, default null)`
- `limit: integer (optional, default 60)`

## get_eurostat_series

Fetch observations from an Eurostat dataset (EU/euro-area statistics), e.g. HICP inflation for Iceland vs the euro area.

    `filters` maps dimension ids to a code, or several codes joined with '+': {"geo": "IS+EA20", "coicop":
    "CP00", "unit": "I15"}. Get dataset codes from search_eurostat_datasets and valid dimension codes
    from get_eurostat_dataset_info / get_eurostat_dimension_values — unknown codes are rejected with a
    list of valid ones. Time range: since_period / until_period (e.g. '2020', '2020-06', '2020-Q1') or
    last_n_periods. Unfiltered dimensions return every value, so always filter big datasets. Observations
    carry Eurostat's flag (p=provisional, e=estimated, b=break, ...) and value=null for missing points. A
    useful counterpart to Hagstofa series. Unrelated to this PoC's legal/EEA tools.

Parameters:

- `dataset: string (required)`
- `filters: object (optional, default null)`
- `since_period: string (optional, default null)`
- `until_period: string (optional, default null)`
- `last_n_periods: integer (optional, default null)`

## get_sdg_indicator

Fetch one Icelandic UN Sustainable Development Goal indicator's full time series by code (e.g. '1-1-1', '16-b-1').

    lang is 'is' or 'en'. Unrelated to this PoC's legal/EEA tools.

Parameters:

- `code: string (required)`
- `lang: string (optional, default "is")`
