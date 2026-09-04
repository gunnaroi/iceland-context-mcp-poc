from __future__ import annotations

import io
import os
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

DATA_DIR = Path(os.getenv("ICELAND_CONTEXT_DATA_DIR", str(Path.cwd() / "data")))
DB_PATH = DATA_DIR / "lagasafn.sqlite3"
ZIP_INDEX_URL = "https://www.althingi.is/lagasafn/zip-skra-af-lagasafni/"
USER_AGENT = "IcelandTrustedContextMCPPoC/0.1 (+public research proof of concept)"


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "cp1252", "iso-8859-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def _to_text(raw: str) -> tuple[str, str]:
    soup = BeautifulSoup(raw, "lxml")
    title_tag = soup.find("title") or soup.find("h1")
    title = title_tag.get_text(" ", strip=True) if title_tag else ""
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    return title, text


def _official_url(filename: str) -> tuple[str | None, str | None]:
    stem = Path(filename).stem
    match = re.search(r"((?:18|19|20)\d{5})", stem)
    if not match:
        return None, None
    identifier = match.group(1)
    return identifier, f"https://www.althingi.is/lagas/nuna/{identifier}.html"


def discover_latest_sgml_zip(client: httpx.Client) -> tuple[str, str]:
    page = client.get(ZIP_INDEX_URL)
    page.raise_for_status()
    soup = BeautifulSoup(page.text, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.endswith("allt_sgml.zip"):
            url = urljoin(str(page.url), href)
            label = a.get_text(" ", strip=True) or href
            return url, label
    raise RuntimeError("Could not discover an SGML Lagasafn ZIP link.")


def build_index() -> Path:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "is,en;q=0.8"}
    with httpx.Client(timeout=60, follow_redirects=True, headers=headers) as client:
        zip_url, label = discover_latest_sgml_zip(client)
        print(f"Downloading: {zip_url}")
        response = client.get(zip_url)
        response.raise_for_status()
        payload = response.content

    archive = zipfile.ZipFile(io.BytesIO(payload))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.execute("CREATE TABLE documents (doc_id INTEGER PRIMARY KEY, identifier TEXT, title TEXT, source_url TEXT, filename TEXT)")
    con.execute("CREATE VIRTUAL TABLE laws_fts USING fts5(doc_id UNINDEXED, title, text, tokenize='unicode61')")

    count = 0
    for name in archive.namelist():
        if name.endswith("/"):
            continue
        raw = _decode(archive.read(name))
        title, text = _to_text(raw)
        if len(text) < 80:
            continue
        identifier, source_url = _official_url(name)
        cur = con.execute(
            "INSERT INTO documents(identifier,title,source_url,filename) VALUES (?,?,?,?)",
            (identifier, title or name, source_url, name),
        )
        doc_id = cur.lastrowid
        con.execute("INSERT INTO laws_fts(doc_id,title,text) VALUES (?,?,?)", (doc_id, title or name, text))
        count += 1

    now = datetime.now(timezone.utc).isoformat()
    con.executemany(
        "INSERT INTO metadata(key,value) VALUES (?,?)",
        [
            ("built_at", now),
            ("source_zip", zip_url),
            ("source_label", label),
            ("document_count", str(count)),
        ],
    )
    con.commit()
    con.close()
    print(f"Indexed {count} documents into {DB_PATH}")
    return DB_PATH


def main() -> None:
    try:
        build_index()
    except Exception as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
