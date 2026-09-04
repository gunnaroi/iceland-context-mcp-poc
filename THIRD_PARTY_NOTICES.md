# Third-party notices

## jokull/icelandic-data

The files under `src/iceland_context_mcp/skills/*/SKILL.md` are vendored verbatim from
[jokull/icelandic-data](https://github.com/jokull/icelandic-data), pinned to commit
[`294fa696a62ac0efb82085209701e87af769fef`](https://github.com/jokull/icelandic-data/tree/294fa696a62ac0efb82085209701e87af769fef),
fetched 2026-09-04. They are exposed read-only through this server's
`context://iceland-data/index` and `context://iceland-data/skill/{name}` MCP resources as
reference documentation about public Icelandic data sources — they are not part of this
PoC's own tool surface, are not retrieved live, and carry none of this project's own
provenance/authority-class metadata.

They document data sources outside this PoC's legal/EEA scope (statistics, government
dashboards, business filings, property records, transport, environment, personal finance).
Two of them — `althingi` and `domstolar` — directly informed how this PoC's own
`get_bill` and `search_court_rulings`/`get_court_ruling` tools were built (the User-Agent
requirement, `málsnúmer` scoping caveat, and the old per-court sites' now-superseded
scraping approach); the rest are included for completeness since all of them were vendored
as reference material, not selectively.

If any of these files are updated or corrected upstream, this vendored copy will not
reflect that until someone re-runs the fetch and re-pins the commit.

License (MIT):

```
MIT License

Copyright (c) 2026 Jökull Sólberg Auðunsson

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
