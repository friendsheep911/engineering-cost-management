# -*- coding: utf-8 -*-
"""把全部期次 JSON 构建为规范化 SQLite 数据库（含全文检索索引）。"""
import argparse
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def issue_order(path):
    m = re.fullmatch(r"(20\d{2})年第(\d+)期\.json", path.name)
    return (int(m.group(1)), int(m.group(2))) if m else (9999, 99)


def build(out):
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    db = sqlite3.connect(out)
    db.executescript("""
    PRAGMA journal_mode=OFF;
    PRAGMA synchronous=OFF;
    CREATE TABLE issues (
      issue_key TEXT PRIMARY KEY, period TEXT, issue_no INTEGER, publish_date TEXT,
      title TEXT, source_file TEXT, source_pages INTEGER
    );
    CREATE TABLE sections (
      id INTEGER PRIMARY KEY, issue_key TEXT NOT NULL REFERENCES issues(issue_key),
      region TEXT, kind TEXT, label TEXT, page_start INTEGER, page_end INTEGER
    );
    CREATE TABLE items (
      id INTEGER PRIMARY KEY, section_id INTEGER NOT NULL REFERENCES sections(id),
      group_name TEXT, name TEXT, spec TEXT, unit TEXT, note TEXT
    );
    CREATE TABLE prices (
      id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id), area TEXT,
      incl_num REAL, incl_raw TEXT, excl_num REAL, excl_raw TEXT
    );
    CREATE INDEX idx_sections_issue ON sections(issue_key);
    CREATE INDEX idx_sections_region_kind ON sections(region, kind);
    CREATE INDEX idx_items_section ON items(section_id);
    CREATE INDEX idx_prices_item ON prices(item_id);
    CREATE INDEX idx_prices_area ON prices(area);
    CREATE VIRTUAL TABLE items_fts USING fts5(name, spec, note, content='items', content_rowid='id', tokenize='unicode61');
    """)
    n_items = n_prices = 0
    for path in sorted(DATA.glob("20*年第*期.json"), key=issue_order):
        doc = json.loads(path.read_text(encoding="utf-8"))
        meta = doc.get("meta") or {}
        key = doc.get("issueKey") or path.stem
        db.execute("INSERT INTO issues VALUES (?,?,?,?,?,?,?)", (
            key, meta.get("period"), meta.get("issueNo"), meta.get("publishDate"),
            meta.get("title"), meta.get("sourceFile"), meta.get("sourcePages")))
        for sec in doc.get("sections", []):
            pages = sec.get("pages") or [None, None]
            cur = db.execute("INSERT INTO sections(issue_key,region,kind,label,page_start,page_end) VALUES (?,?,?,?,?,?)", (
                key, sec.get("region"), sec.get("kind"), sec.get("label") or sec.get("title"),
                pages[0] if pages else None, pages[1] if len(pages) > 1 else None))
            sid = cur.lastrowid
            for row in sec.get("rows", []):
                cur = db.execute("INSERT INTO items(section_id,group_name,name,spec,unit,note) VALUES (?,?,?,?,?,?)", (
                    sid, row.get("group"), row.get("name"), row.get("spec"), row.get("unit"), row.get("note")))
                iid = cur.lastrowid
                n_items += 1
                for price in row.get("prices", []):
                    incl, excl = price.get("incl") or {}, price.get("excl") or {}
                    db.execute("INSERT INTO prices(item_id,area,incl_num,incl_raw,excl_num,excl_raw) VALUES (?,?,?,?,?,?)", (
                        iid, price.get("area"), incl.get("num"), incl.get("raw"), excl.get("num"), excl.get("raw")))
                    n_prices += 1
    db.execute("INSERT INTO items_fts(items_fts) VALUES('rebuild')")
    db.execute("PRAGMA optimize")
    db.commit()
    count = db.execute("SELECT count(*) FROM issues").fetchone()[0]
    db.close()
    print(f"SQLite 完成：{out} | {count} 期 | {n_items:,} 材料行 | {n_prices:,} 价格记录")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "database" / "info_prices.sqlite")
    args = ap.parse_args()
    build(args.out.resolve())
