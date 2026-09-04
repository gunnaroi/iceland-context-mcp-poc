from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).with_name("skills")

# These SKILL.md files are vendored verbatim (frontmatter and body unchanged)
# from jokull/icelandic-data under its MIT license — see THIRD_PARTY_NOTICES.md.
SOURCE_REPO = "jokull/icelandic-data"
SOURCE_COMMIT = "294fa696a62ac0efb82085209701e87af769fef"
SOURCE_LICENSE = "MIT"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass(frozen=True)
class DataSkill:
    name: str
    description: str
    body: str


def _parse(path: Path) -> DataSkill:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    name = path.parent.name
    description = ""
    if match:
        for line in match.group(1).splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("description:"):
                description = line.split(":", 1)[1].strip()
    return DataSkill(name=name, description=description, body=text)


def list_data_skills() -> list[DataSkill]:
    if not SKILLS_DIR.is_dir():
        return []
    skills = [_parse(p) for p in sorted(SKILLS_DIR.glob("*/SKILL.md"))]
    return skills


def get_data_skill(name: str) -> DataSkill:
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.is_file():
        known = ", ".join(s.name for s in list_data_skills())
        raise ValueError(f"Unknown data skill: {name!r}. Known: {known}")
    return _parse(path)


def attribution_header(name: str) -> str:
    return (
        f"<!--\n"
        f"Vendored from {SOURCE_REPO} (MIT License), commit {SOURCE_COMMIT}.\n"
        f"Path: .agents/skills/{name}/SKILL.md\n"
        f"This documents a public Icelandic data source unrelated to this PoC's own\n"
        f"legal/EEA tools — reference material only, not retrieved live and not\n"
        f"covered by this project's provenance/authority-class discipline.\n"
        f"-->\n\n"
    )
