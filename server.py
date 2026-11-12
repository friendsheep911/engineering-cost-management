# -*- coding: utf-8 -*-
"""NAS/Docker 版：静态网站 + SQLite 全库检索 API。"""
import json
import os
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("INFO_PRICE_DB", ROOT / "database" / "info_prices.sqlite"))


class Handler(SimpleHTTPRequestHandler):
    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/health":
            return self.send_json({"ok": DB.exists(), "database": str(DB.name)})
        if u.path == "/api/issues":
            with sqlite3.connect(DB) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute("SELECT * FROM issues ORDER BY publish_date DESC, issue_key DESC").fetchall()
            return self.send_json({"rows": [dict(x) for x in rows]})
        if u.path == "/api/search":
            return self.search(parse_qs(u.query))
        return super().do_GET()

    def search(self, args):
        q = (args.get("q") or [""])[0].strip()
        if not q:
            return self.send_json({"count": 0, "rows": []})
        limit = min(max(int((args.get("limit") or [100])[0]), 1), 500)
        sql = """
        SELECT s.issue_key,s.region,s.kind,i.name,i.spec,i.unit,i.note,p.area,
               p.incl_num,p.incl_raw,p.excl_num,p.excl_raw
        FROM items_fts f JOIN items i ON i.id=f.rowid
        JOIN sections s ON s.id=i.section_id JOIN prices p ON p.item_id=i.id
        WHERE items_fts MATCH ? ORDER BY s.issue_key DESC LIMIT ?
        """
        # FTS5 中文支持连续词匹配；双引号避免用户输入被当作查询语法。
        term = '"' + q.replace('"', '""') + '"'
        try:
            with sqlite3.connect(DB) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute(sql, (term, limit)).fetchall()
                if not rows:
                    like = f"%{q}%"
                    rows = db.execute("""
                    SELECT s.issue_key,s.region,s.kind,i.name,i.spec,i.unit,i.note,p.area,
                           p.incl_num,p.incl_raw,p.excl_num,p.excl_raw
                    FROM items i JOIN sections s ON s.id=i.section_id
                    JOIN prices p ON p.item_id=i.id
                    WHERE i.name LIKE ? OR i.spec LIKE ? OR i.note LIKE ?
                    ORDER BY s.issue_key DESC LIMIT ?
                    """, (like, like, like, limit)).fetchall()
        except (sqlite3.Error, ValueError) as e:
            return self.send_json({"error": str(e)}, 400)
        return self.send_json({"count": len(rows), "rows": [dict(x) for x in rows]})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    os.chdir(ROOT)
    print(f"信息价系统：http://0.0.0.0:{port} | 数据库：{DB}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
