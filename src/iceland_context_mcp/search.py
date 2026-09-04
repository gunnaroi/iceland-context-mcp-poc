from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from .models import LawSearchResult, SearchHit

DATA_DIR = Path(os.getenv("ICELAND_CONTEXT_DATA_DIR", str(Path.cwd() / "data")))
DB_PATH = DATA_DIR / "lagasafn.sqlite3"


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\wÁÉÍÓÚÝÞÆÖÐáéíóúýþæöð]+", query, flags=re.UNICODE)
    if not tokens:
        raise ValueError("Search query contains no searchable terms.")
    return " AND ".join(f'"{token}"' for token in tokens[:12])


def search_laws(query: str, limit: int = 8) -> LawSearchResult:
    if not DB_PATH.exists():
        raise RuntimeError("Lagasafn discovery index is not built. Run: uv run iceland-context-bootstrap")
    limit = max(1, min(limit, 20))
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    snapshot = con.execute("SELECT value FROM metadata WHERE key='built_at'").fetchone()[0]
    rows = con.execute(
        """
        SELECT d.identifier, d.title, d.source_url,
               snippet(laws_fts, 2, '[', ']', ' … ', 24) AS snippet
        FROM laws_fts
        JOIN documents d ON d.doc_id = laws_fts.doc_id
        WHERE laws_fts MATCH ?
        ORDER BY bm25(laws_fts)
        LIMIT ?
        """,
        (_fts_query(query), limit),
    ).fetchall()
    con.close()
    return LawSearchResult(
        query=query,
        hits=[
            SearchHit(
                official_identifier=row["identifier"],
                title=row["title"],
                snippet=row["snippet"],
                source_url=row["source_url"],
                index_snapshot=snapshot,
            )
            for row in rows
        ],
    )
