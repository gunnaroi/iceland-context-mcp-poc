# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27,<1", "pydantic>=2.7,<3", "lxml>=5,<7"]
# ///
"""Icelandic official statistics from the command line: Hagstofa Íslands, Eurostat, Iceland's SDG indicators.

    uv run iceland_stats.py list                       the tools, one line each
    uv run iceland_stats.py describe TOOL              full guidance + parameters for one tool
    uv run iceland_stats.py call TOOL key=value ...    run a tool (values are JSON if they parse, else strings)
    uv run iceland_stats.py call TOOL '{"a": 1}'       ... or a JSON object ('-' reads stdin, @file reads a file)

Results are JSON on stdout. Failures print the tool's own message (it says what to try next) on stderr and
exit with status 2. The tools are the ones behind the iceland-context MCP server, called in-process.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import sys
import types
from pathlib import Path

logging.disable(logging.WARNING - 1)
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:  # the vendored code only needs ToolError from the MCP SDK; provide a stand-in when it isn't installed
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:

    class ToolError(Exception):
        pass

    for name in ("mcp", "mcp.server", "mcp.server.mcpserver", "mcp.server.mcpserver.exceptions"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["mcp.server.mcpserver.exceptions"].ToolError = ToolError

import httpx  # noqa: E402

from iceland_context_mcp import open_data as od  # noqa: E402

# tool name -> implementing coroutine (parameter names are the tool's parameter names)
TOOLS = {
    "search_hagstofa_tables": od.search_hagstofa_catalog,
    "get_hagstofa_table_info": od.get_hagstofa_table_info,
    "get_hagstofa_table": od.get_hagstofa_table,
    "browse_hagstofa_tables": od.browse_hagstofa,
    "search_eurostat_datasets": od.search_eurostat_catalog,
    "get_eurostat_dataset_info": od.describe_eurostat_dataset,
    "get_eurostat_dimension_values": od.list_eurostat_dimension_values,
    "get_eurostat_series": od.get_eurostat_series,
    "get_sdg_indicator": od.get_sdg_indicator,
}
DESCRIPTIONS = json.loads((HERE.parent / "references" / "tools.json").read_text(encoding="utf-8"))


def _parse_args(tokens: list[str]) -> dict:
    if len(tokens) == 1 and not ("=" in tokens[0] and not tokens[0].lstrip().startswith("{")):
        raw = tokens[0]
        if raw == "-":
            raw = sys.stdin.read()
        elif raw.startswith("@"):
            raw = Path(raw[1:]).read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise SystemExit("JSON arguments must be an object.")
        return data
    args: dict = {}
    for token in tokens:
        if "=" not in token:
            raise SystemExit(f"Expected key=value, got {token!r}.")
        key, value = token.split("=", 1)
        try:
            args[key] = json.loads(value)
        except ValueError:
            args[key] = value
    return args


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.strip().splitlines() if line.strip()), "")


async def _call(name: str, tokens: list[str]) -> int:
    if name not in TOOLS:
        print(f"Unknown tool {name!r}. Tools: {', '.join(TOOLS)}", file=sys.stderr)
        return 2
    args = _parse_args(tokens)
    fn = TOOLS[name]
    valid = list(inspect.signature(fn).parameters)
    unknown = [k for k in args if k not in valid]
    if unknown:
        print(f"Unknown parameter(s) {unknown} for {name}. Valid parameters: {', '.join(valid)}.", file=sys.stderr)
        return 2
    try:
        result = await fn(**args)
    except ToolError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except TypeError as exc:  # missing required parameter
        print(f"{name}: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except httpx.HTTPStatusError as exc:
        print(
            f"The source ({exc.response.url.host}) answered HTTP {exc.response.status_code}. Check the identifiers; "
            "if they are right the source may be temporarily unavailable or rate-limiting — wait and retry.",
            file=sys.stderr,
        )
        return 2
    except httpx.TimeoutException:
        print("The source did not answer in time. Retry, ideally with a narrower request.", file=sys.stderr)
        return 2
    except httpx.TransportError:
        print("Could not reach the source (network error). Is outbound HTTPS allowed?", file=sys.stderr)
        return 2
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("describe").add_argument("tool")
    p = sub.add_parser("call")
    p.add_argument("tool")
    p.add_argument("args", nargs="*")
    ns = parser.parse_args()
    if ns.cmd == "list":
        for name in TOOLS:
            print(f"{name}: {_first_line(DESCRIPTIONS[name]['description'])}")
    elif ns.cmd == "describe":
        if ns.tool not in DESCRIPTIONS:
            raise SystemExit(f"Unknown tool {ns.tool!r}. Run `list`.")
        d = DESCRIPTIONS[ns.tool]
        print(d["description"].strip(), "\n\nParameters:")
        for p in d["parameters"]:
            print(f"  {p}")
    else:
        return asyncio.run(_call(ns.tool, ns.args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
