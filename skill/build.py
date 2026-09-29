"""Refresh the vendored/generated parts of skill/iceland-statistics from the MCP package.

Hand-written: SKILL.md, scripts/iceland_stats.py, references/hagstofa-notes.md. Generated here:
scripts/iceland_context_mcp/ (only the statistics modules), references/tools.json and tools.md (tool
guidance, taken from the MCP server's own tool descriptions so the two never drift).

    uv run python skill/build.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = Path(__file__).resolve().parent / "iceland-statistics"
PACKAGE = ROOT / "src" / "iceland_context_mcp"
sys.path.insert(0, str(ROOT / "src"))

from iceland_context_mcp.server import mcp  # noqa: E402

# skill tool name -> MCP tool name
TOOL_MAP = {
    "search_hagstofa_tables": "search_hagstofa_tables",
    "get_hagstofa_table_info": "get_hagstofa_table_info",
    "get_hagstofa_table": "get_hagstofa_table_tool",
    "browse_hagstofa_tables": "browse_hagstofa_tables",
    "search_eurostat_datasets": "search_eurostat_datasets_tool",
    "get_eurostat_dataset_info": "get_eurostat_dataset_info",
    "get_eurostat_dimension_values": "get_eurostat_dimension_values",
    "get_eurostat_series": "get_eurostat_series_tool",
    "get_sdg_indicator": "get_sdg_indicator_tool",
}
VENDORED = [
    "__init__.py",
    "open_data.py",
    "open_data_registry.json",
    "eurostat.py",
    "hagstofa_catalog.py",
    "hagstofa_catalog.json",
]


def vendor_package() -> None:
    target = SKILL / "scripts" / "iceland_context_mcp"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name in VENDORED:
        shutil.copy(PACKAGE / name, target / name)


def _type_of(spec: dict) -> str:
    if "type" in spec:
        return spec["type"]
    return "|".join(o.get("type", "?") for o in spec.get("anyOf", []) if o.get("type") != "null") or "any"


async def main() -> None:
    vendor_package()
    by_name = {t.name: t for t in await mcp.list_tools()}
    out: dict[str, dict] = {}
    for skill_name, mcp_name in TOOL_MAP.items():
        tool = by_name[mcp_name]
        required = set(tool.input_schema.get("required", []))
        params = [
            f"{key}: {_type_of(spec)}" + (" (required)" if key in required else f" (optional, default {json.dumps(spec.get('default'))})")
            for key, spec in tool.input_schema.get("properties", {}).items()
        ]
        description = (tool.description or "").strip()
        for mcp_tool in TOOL_MAP.values():  # tool names inside the text use the skill's names
            description = description.replace(mcp_tool, next(k for k, v in TOOL_MAP.items() if v == mcp_tool))
        out[skill_name] = {"description": description, "parameters": params}
    refs = SKILL / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "tools.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [
        "# Tool reference",
        "",
        "Generated from the MCP server's tool descriptions (`skill/build.py`); `describe TOOL` prints the same text.",
        "",
    ]
    for name, d in out.items():
        lines += [f"## {name}", "", d["description"], "", "Parameters:", ""] + [f"- `{p}`" for p in d["parameters"]] + [""]
    (refs / "tools.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Built {SKILL}")


if __name__ == "__main__":
    asyncio.run(main())
