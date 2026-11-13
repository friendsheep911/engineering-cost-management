# -*- coding: utf-8 -*-
"""NAS/Docker 版：静态网站、SQLite 全库检索与项目调差 API。"""
import json
import os
import re
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from project_api import ProjectAPI, ProjectError

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("INFO_PRICE_DB", ROOT / "database" / "info_prices.sqlite"))
PROJECT_DB = Path(os.environ.get("PROJECT_DB", ROOT / "storage" / "projects.sqlite"))
PROJECTS = ProjectAPI(DB, PROJECT_DB)


class Handler(SimpleHTTPRequestHandler):
    def send_cors(self):
        origin = self.headers.get("Origin", "")
        allowed = [x.strip() for x in os.environ.get("CORS_ORIGINS", "").split(",") if x.strip()]
        if origin and origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        size = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(size).decode("utf-8") or "{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                return self.send_json({"ok": DB.exists(), "database": DB.name, "projects": PROJECT_DB.name})
            if parsed.path == "/api/issues":
                with sqlite3.connect(DB) as db:
                    db.row_factory = sqlite3.Row
                    rows = db.execute("SELECT * FROM issues ORDER BY publish_date DESC,issue_key DESC").fetchall()
                return self.send_json({"rows": [dict(x) for x in rows]})
            if parsed.path == "/api/search":
                return self.search(parse_qs(parsed.query))
            if parsed.path == "/api/project-options":
                return self.send_json(PROJECTS.options())
            if parsed.path == "/api/projects":
                return self.send_json({"rows": PROJECTS.list_projects()})
            if parsed.path == "/api/projects/template.xlsx":
                body, name = PROJECTS.template()
                return self.send_file(body, name)
            match = re.fullmatch(r"/api/projects/(\d+)(/export\.xlsx)?", parsed.path)
            if match:
                project_id = int(match.group(1))
                if match.group(2):
                    body, name = PROJECTS.export(project_id)
                    return self.send_file(body, name)
                return self.send_json(PROJECTS.detail(project_id))
        except ProjectError as exc:
            return self.send_json({"error": str(exc)}, exc.status)
        except (sqlite3.Error, ValueError) as exc:
            return self.send_json({"error": str(exc)}, 400)
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/projects":
                return self.send_json(PROJECTS.create(self.read_json()), 201)
            match = re.fullmatch(r"/api/projects/(\d+)/(import|calculate)", path)
            if match:
                project_id, action = int(match.group(1)), match.group(2)
                if action == "import":
                    size = int(self.headers.get("Content-Length", "0"))
                    result = PROJECTS.import_xlsx(project_id, self.headers.get("Content-Type", ""), self.rfile.read(size))
                    return self.send_json(result)
                return self.send_json(PROJECTS.calculate(project_id, self.read_json()))
        except ProjectError as exc:
            return self.send_json({"error": str(exc)}, exc.status)
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
            return self.send_json({"error": str(exc)}, 400)
        return self.send_json({"error": "接口不存在"}, 404)

    def do_DELETE(self):
        match = re.fullmatch(r"/api/projects/(\d+)", urlparse(self.path).path)
        if not match:
            return self.send_json({"error": "接口不存在"}, 404)
        return self.send_json({"ok": PROJECTS.delete(int(match.group(1)))})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors()
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def send_file(self, body, filename):
        from urllib.parse import quote
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_cors()
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def search(self, args):
        query = (args.get("q") or [""])[0].strip()
        if not query:
            return self.send_json({"count": 0, "rows": []})
        limit = min(max(int((args.get("limit") or [100])[0]), 1), 500)
        term = '"' + query.replace('"', '""') + '"'
        sql = """
        SELECT s.issue_key,s.region,s.kind,i.name,i.spec,i.unit,i.note,p.area,
               p.incl_num,p.incl_raw,p.excl_num,p.excl_raw
        FROM items_fts f JOIN items i ON i.id=f.rowid
        JOIN sections s ON s.id=i.section_id JOIN prices p ON p.item_id=i.id
        WHERE items_fts MATCH ? ORDER BY s.issue_key DESC LIMIT ?
        """
        with sqlite3.connect(DB) as db:
            db.row_factory = sqlite3.Row
            try:
                rows = db.execute(sql, (term, limit)).fetchall()
            except sqlite3.Error:
                rows = []
            if not rows:
                like = f"%{query}%"
                rows = db.execute("""
                SELECT s.issue_key,s.region,s.kind,i.name,i.spec,i.unit,i.note,p.area,
                       p.incl_num,p.incl_raw,p.excl_num,p.excl_raw
                FROM items i JOIN sections s ON s.id=i.section_id JOIN prices p ON p.item_id=i.id
                WHERE i.name LIKE ? OR i.spec LIKE ? OR i.note LIKE ?
                ORDER BY s.issue_key DESC LIMIT ?
                """, (like, like, like, limit)).fetchall()
        return self.send_json({"count": len(rows), "rows": [dict(x) for x in rows]})


if __name__ == "__main__":
    PROJECTS.initialize()
    port = int(os.environ.get("PORT", "8080"))
    os.chdir(ROOT)
    print(f"信息价系统：http://0.0.0.0:{port} | 信息价库：{DB} | 项目库：{PROJECT_DB}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
