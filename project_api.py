# -*- coding: utf-8 -*-
"""项目清单导入、信息价匹配、调差计算与 Excel 输出。"""
from __future__ import annotations

import io
import re
import sqlite3
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from email.parser import BytesParser
from email.policy import default as email_policy

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


ALIASES = {
    "no": ("序号", "编号"),
    "name": ("材料名称", "设备名称", "苗木品种", "工种名称", "机械名称", "名称", "材料"),
    "spec": ("规格型号", "型号规格", "规格", "型号", "强度等级"),
    "unit": ("计量单位", "单位"),
    "quantity": ("工程量", "数量", "用量", "消耗量"),
    "remark": ("备注", "说明", "项目特征", "特征描述"),
}


class ProjectError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class ClosingConnection(sqlite3.Connection):
    """让 with sqlite3 既提交/回滚，也立即释放 Windows 文件句柄。"""
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text(value):
    return "" if value is None else str(value).strip()


def norm(value):
    value = unicodedata.normalize("NFKC", text(value)).lower()
    return re.sub(r"[\s\-—_·,，。./\\()（）\[\]【】:：]+", "", value)


def numeric(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text(value).replace(",", ""))
    return float(match.group()) if match else None


def header_key(value):
    value = norm(value)
    for key, aliases in ALIASES.items():
        if any(value == norm(alias) for alias in aliases):
            return key
    for key, aliases in ALIASES.items():
        if any(len(norm(alias)) > 1 and norm(alias) in value for alias in aliases):
            return key
    return None


class ProjectAPI:
    def __init__(self, reference_db, project_db):
        self.reference_db = reference_db
        self.project_db = project_db

    def connect(self):
        self.project_db.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.project_db, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def initialize(self):
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects (
              id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
              region TEXT NOT NULL DEFAULT '', price_area TEXT NOT NULL DEFAULT '',
              base_issue TEXT NOT NULL, current_issue TEXT NOT NULL,
              tax_mode TEXT NOT NULL DEFAULT 'incl', threshold REAL NOT NULL DEFAULT 5,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
              source_sheet TEXT, source_row INTEGER, source_no TEXT,
              name TEXT NOT NULL, spec TEXT, unit TEXT, quantity REAL, remark TEXT,
              matched_name TEXT, matched_spec TEXT, matched_unit TEXT,
              matched_kind TEXT, matched_region TEXT, matched_area TEXT,
              match_score REAL, match_status TEXT,
              base_price REAL, current_price REAL, base_amount REAL,
              current_amount REAL, difference REAL, change_rate REAL, risk TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_project_items_project ON project_items(project_id);
            """)

    def options(self):
        with sqlite3.connect(self.reference_db, factory=ClosingConnection) as db:
            db.row_factory = sqlite3.Row
            issues = [dict(x) for x in db.execute(
                "SELECT issue_key,publish_date,title FROM issues ORDER BY publish_date DESC,issue_key DESC")]
            regions = [x[0] for x in db.execute(
                "SELECT DISTINCT region FROM sections WHERE region<>'' ORDER BY region")]
            pairs = db.execute("""
                SELECT DISTINCT s.region,p.area FROM sections s
                JOIN items i ON i.section_id=s.id JOIN prices p ON p.item_id=i.id
                WHERE p.area<>'' ORDER BY s.region,p.area
            """).fetchall()
        area_regions = {}
        ignored_areas = {"冠幅", "土球", "苗龄", "高度", "－", "-"}
        for region, area in pairs:
            if area not in ignored_areas:
                area_regions.setdefault(region, []).append(area)
        return {"issues": issues, "regions": regions, "area_regions": area_regions}

    def list_projects(self):
        with self.connect() as db:
            rows = db.execute("""
            SELECT p.*,COUNT(i.id) item_count,
              SUM(CASE WHEN i.match_status='已匹配' THEN 1 ELSE 0 END) matched_count
            FROM projects p LEFT JOIN project_items i ON i.project_id=p.id
            GROUP BY p.id ORDER BY p.updated_at DESC
            """).fetchall()
        return [dict(x) for x in rows]

    def create(self, data):
        required = ("name", "base_issue", "current_issue")
        if any(not text(data.get(key)) for key in required):
            raise ProjectError("项目名称、基期和当前期不能为空")
        stamp = now()
        with self.connect() as db:
            cur = db.execute("""
            INSERT INTO projects(name,region,price_area,base_issue,current_issue,tax_mode,
              threshold,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)
            """, (text(data["name"]), text(data.get("region")), text(data.get("price_area")),
                  text(data["base_issue"]), text(data["current_issue"]),
                  "excl" if data.get("tax_mode") == "excl" else "incl",
                  float(data.get("threshold") or 5), stamp, stamp))
            project_id = cur.lastrowid
        return self.detail(project_id)

    def delete(self, project_id):
        with self.connect() as db:
            cur = db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        return bool(cur.rowcount)

    def detail(self, project_id):
        with self.connect() as db:
            project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise ProjectError("项目不存在", 404)
            rows = db.execute("SELECT * FROM project_items WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
        items = [dict(x) for x in rows]
        summary = {
            "total": len(items),
            "matched": sum(x["match_status"] == "已匹配" for x in items),
            "review": sum(x["match_status"] == "待复核" for x in items),
            "unmatched": sum(x["match_status"] in (None, "未匹配") for x in items),
            "base_amount": sum(x["base_amount"] or 0 for x in items),
            "current_amount": sum(x["current_amount"] or 0 for x in items),
            "difference": sum(x["difference"] or 0 for x in items),
            "risk": sum(bool(x["risk"]) for x in items),
        }
        return {"project": dict(project), "summary": summary, "items": items}

    def parse_upload(self, content_type, body):
        if not body or len(body) > 20 * 1024 * 1024:
            raise ProjectError("上传文件为空或超过 20 MB")
        if "multipart/form-data" not in content_type:
            raise ProjectError("请使用 multipart/form-data 上传 Excel")
        msg = BytesParser(policy=email_policy).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body)
        for part in msg.iter_parts():
            if part.get_param("name", header="content-disposition") == "file":
                filename = part.get_filename() or "upload.xlsx"
                if not filename.lower().endswith(".xlsx"):
                    raise ProjectError("第一阶段仅支持 .xlsx 文件")
                return filename, part.get_payload(decode=True)
        raise ProjectError("没有找到名为 file 的上传字段")

    def extract_items(self, raw):
        try:
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception as exc:
            raise ProjectError(f"无法读取 Excel：{exc}") from exc
        result, warnings = [], []
        for ws in wb.worksheets:
            best = None
            for row_no, values in enumerate(ws.iter_rows(min_row=1, max_row=min(ws.max_row, 30), values_only=True), 1):
                mapping = {}
                for col_no, value in enumerate(values, 1):
                    key = header_key(value)
                    if key and key not in mapping:
                        mapping[key] = col_no
                score = ("name" in mapping) * 3 + ("quantity" in mapping) * 2 + ("unit" in mapping) + ("spec" in mapping)
                if "name" in mapping and (best is None or score > best[0]):
                    best = (score, row_no, mapping)
            if not best:
                warnings.append(f"{ws.title}：未识别到材料名称列")
                continue
            _, header_row, mapping = best
            blank_run = 0
            for row_no, values in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), header_row + 1):
                def cell(key):
                    index = mapping.get(key)
                    return values[index - 1] if index and index <= len(values) else None
                name = text(cell("name"))
                if not name:
                    blank_run += 1
                    if blank_run >= 30:
                        break
                    continue
                blank_run = 0
                if name in ("合计", "小计", "总计") or name.startswith("注："):
                    continue
                result.append((ws.title, row_no, text(cell("no")), name, text(cell("spec")),
                               text(cell("unit")), numeric(cell("quantity")), text(cell("remark"))))
        wb.close()
        if not result:
            raise ProjectError("没有识别到材料行。请使用模板，或确保表中包含“材料名称”列。")
        return result, warnings

    def import_xlsx(self, project_id, content_type, body):
        filename, raw = self.parse_upload(content_type, body)
        rows, warnings = self.extract_items(raw)
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ProjectError("项目不存在", 404)
            db.execute("DELETE FROM project_items WHERE project_id=?", (project_id,))
            db.executemany("""
            INSERT INTO project_items(project_id,source_sheet,source_row,source_no,name,spec,unit,quantity,remark)
            VALUES (?,?,?,?,?,?,?,?,?)
            """, [(project_id,) + row for row in rows])
            db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now(), project_id))
        detail = self.calculate(project_id, {})
        detail["import"] = {"filename": filename, "count": len(rows), "warnings": warnings}
        return detail

    @staticmethod
    def similarity(source, candidate):
        sn, ss, su = norm(source["name"]), norm(source["spec"]), norm(source["unit"])
        cn, cs, cu = norm(candidate["name"]), norm(candidate["spec"]), norm(candidate["unit"])
        ns = SequenceMatcher(None, sn, cn).ratio() if sn and cn else 0
        if sn == cn:
            ns = 1
        elif sn in cn or cn in sn:
            ns = max(ns, .9)
        ss_score = SequenceMatcher(None, ss, cs).ratio() if ss and cs else (.75 if not ss else 0)
        if ss and ss == cs:
            ss_score = 1
        elif ss and cs and (ss in cs or cs in ss):
            ss_score = max(ss_score, .9)
        us = 1 if su and su == cu else (.55 if not su else 0)
        return ns * .68 + ss_score * .24 + us * .08

    def candidates(self, issue, region, area, tax_mode):
        price_col = "p.excl_num" if tax_mode == "excl" else "p.incl_num"
        where, params = ["s.issue_key=?", f"{price_col} IS NOT NULL"], [issue]
        if region:
            where.append("s.region=?")
            params.append(region)
        with sqlite3.connect(self.reference_db, factory=ClosingConnection) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(x) for x in db.execute(f"""
            SELECT i.name,i.spec,i.unit,s.kind,s.region,p.area,{price_col} price
            FROM items i JOIN sections s ON s.id=i.section_id JOIN prices p ON p.item_id=i.id
            WHERE {' AND '.join(where)}
            """, params)]
        if area:
            exact = [row for row in rows if text(row["area"]) == area]
            if exact:
                rows = exact
        return rows

    def best(self, source, candidates):
        if not candidates:
            return None, 0
        ranked = [(self.similarity(source, candidate), candidate) for candidate in candidates]
        return max(ranked, key=lambda x: x[0])[1], max(ranked, key=lambda x: x[0])[0]

    def calculate(self, project_id, updates):
        allowed = ("region", "price_area", "base_issue", "current_issue", "tax_mode", "threshold")
        with self.connect() as db:
            fields, values = [], []
            for key in allowed:
                if key in updates:
                    fields.append(f"{key}=?")
                    values.append(updates[key])
            if fields:
                values.extend([now(), project_id])
                db.execute(f"UPDATE projects SET {','.join(fields)},updated_at=? WHERE id=?", values)
            project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise ProjectError("项目不存在", 404)
            items = db.execute("SELECT * FROM project_items WHERE project_id=?", (project_id,)).fetchall()
            project = dict(project)
            current = self.candidates(project["current_issue"], project["region"], project["price_area"], project["tax_mode"])
            base = self.candidates(project["base_issue"], project["region"], project["price_area"], project["tax_mode"])
            threshold = float(project["threshold"] or 0)
            for item in items:
                source = dict(item)
                match, score = self.best(source, current)
                status, fields = "未匹配", [None] * 6
                base_price = current_price = base_amount = current_amount = difference = change_rate = None
                risk = "未找到当前期信息价"
                if match and score >= .52:
                    status = "已匹配" if score >= .72 else "待复核"
                    old, old_score = self.best(match, base)
                    current_price = numeric(match["price"])
                    base_price = numeric(old["price"]) if old and old_score >= .62 else None
                    quantity = source["quantity"]
                    base_amount = quantity * base_price if quantity is not None and base_price is not None else None
                    current_amount = quantity * current_price if quantity is not None and current_price is not None else None
                    difference = current_amount - base_amount if base_amount is not None and current_amount is not None else None
                    change_rate = ((current_price - base_price) / base_price * 100
                                   if base_price not in (None, 0) and current_price is not None else None)
                    risks = []
                    if status == "待复核": risks.append("匹配度较低")
                    if base_price is None: risks.append("基期无价格")
                    if quantity is None: risks.append("缺少数量")
                    if change_rate is not None and abs(change_rate) >= threshold: risks.append(f"涨跌幅超过{threshold:g}%")
                    risk = "；".join(risks)
                    fields = [match["name"], match["spec"], match["unit"], match["kind"], match["region"], match["area"]]
                db.execute("""
                UPDATE project_items SET matched_name=?,matched_spec=?,matched_unit=?,matched_kind=?,
                  matched_region=?,matched_area=?,match_score=?,match_status=?,base_price=?,current_price=?,
                  base_amount=?,current_amount=?,difference=?,change_rate=?,risk=? WHERE id=?
                """, (*fields, round(score * 100, 1), status, base_price, current_price, base_amount,
                      current_amount, difference, change_rate, risk, item["id"]))
            db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now(), project_id))
        return self.detail(project_id)

    @staticmethod
    def header(ws, row, count):
        for col in range(1, count + 1):
            cell = ws.cell(row, col)
            cell.fill = PatternFill("solid", fgColor="1F5FA8")
            cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
            cell.alignment = Alignment(horizontal="center", vertical="center")

    @staticmethod
    def bytes(wb):
        stream = io.BytesIO()
        wb.save(stream)
        wb.close()
        return stream.getvalue()

    def template(self):
        wb, ws = Workbook(), None
        ws = wb.active
        ws.title = "导入清单"
        ws.sheet_view.showGridLines = False
        ws["A2"] = "项目材料清单导入模板"
        ws["A2"].font = Font(name="Arial", size=14, bold=True)
        ws["A3"] = "请从第6行开始填写。材料名称为必填，数量用于计算调差金额。示例行可删除。"
        ws["A3"].font = Font(name="Arial", size=10, italic=True, color="5A6675")
        headers = ["序号", "材料名称", "规格型号", "单位", "数量", "备注"]
        for col, value in enumerate(headers, 1): ws.cell(5, col, value)
        self.header(ws, 5, len(headers))
        sample = [1, "热轧带肋钢筋", "HRB400E Φ20", "t", 10, "示例行可删除"]
        for col, value in enumerate(sample, 1): ws.cell(6, col, value)
        ws["E6"].number_format = "#,##0.00"
        ws.freeze_panes, ws.auto_filter.ref = "A6", "A5:F1005"
        for col, width in enumerate([10, 28, 24, 10, 14, 32], 1):
            ws.column_dimensions[get_column_letter(col)].width = width
        return self.bytes(wb), "项目材料清单导入模板.xlsx"

    def export(self, project_id):
        detail = self.detail(project_id)
        p, summary, items = detail["project"], detail["summary"], detail["items"]
        wb, ws = Workbook(), None
        ws = wb.active
        ws.title = "调差结果"
        ws.sheet_view.showGridLines = False
        ws["A2"] = p["name"] + " 材料调差结果"
        ws["A2"].font = Font(name="Arial", size=14, bold=True)
        ws["A3"] = f"地区：{p['region'] or '全部'}　计价区域：{p['price_area'] or '自动'}　基期：{p['base_issue']}　当前期：{p['current_issue']}"
        ws["A3"].font = Font(name="Arial", size=10, color="5A6675")
        ws.append([])
        ws["A4"], ws["B4"], ws["C4"], ws["D4"] = "项目合计", summary["base_amount"], summary["current_amount"], summary["difference"]
        for cell in ws[4][:4]: cell.font = Font(name="Arial", size=10, bold=True)
        for cell in ws[4][1:4]: cell.number_format = "#,##0.00"
        headers = ["序号", "源工作表", "源行", "材料名称", "规格型号", "单位", "数量", "匹配状态",
                   "匹配材料", "匹配规格", "计价区域", "基期单价", "当前单价", "基期合价", "当前合价",
                   "调差金额", "涨跌幅", "匹配度", "风险提示", "备注"]
        for col, value in enumerate(headers, 1): ws.cell(7, col, value)
        self.header(ws, 7, len(headers))
        thin = Side(style="thin", color="E3E8EF")
        for index, item in enumerate(items, 1):
            row = index + 7
            values = [index, item["source_sheet"], item["source_row"], item["name"], item["spec"], item["unit"],
                      item["quantity"], item["match_status"], item["matched_name"], item["matched_spec"], item["matched_area"],
                      item["base_price"], item["current_price"], None, None, None, None, item["match_score"], item["risk"], item["remark"]]
            for col, value in enumerate(values, 1): ws.cell(row, col, value)
            ws.cell(row, 14, f'=IF(OR(G{row}="",L{row}=""),"",G{row}*L{row})')
            ws.cell(row, 15, f'=IF(OR(G{row}="",M{row}=""),"",G{row}*M{row})')
            ws.cell(row, 16, f'=IF(OR(N{row}="",O{row}=""),"",O{row}-N{row})')
            ws.cell(row, 17, f'=IF(OR(L{row}="",L{row}=0,M{row}=""),"",(M{row}-L{row})/L{row})')
            for col in range(1, 21):
                ws.cell(row, col).border = Border(bottom=thin)
                ws.cell(row, col).alignment = Alignment(vertical="center", wrap_text=col in (4, 5, 9, 10, 19, 20))
            for col in (7, 12, 13, 14, 15, 16): ws.cell(row, col).number_format = "#,##0.00"
            ws.cell(row, 17).number_format, ws.cell(row, 18).number_format = "0.00%", "0.0"
        last = 7 + len(items)
        total = last + 1
        ws.cell(total, 13, "合计")
        for col in (14, 15, 16):
            letter = get_column_letter(col)
            ws.cell(total, col, f"=SUM({letter}8:{letter}{last})" if items else 0)
        for col in range(13, 17):
            ws.cell(total, col).font = Font(name="Arial", size=10, bold=True)
            ws.cell(total, col).fill = PatternFill("solid", fgColor="EAF2FB")
            ws.cell(total, col).number_format = "#,##0.00"
        ws.freeze_panes, ws.auto_filter.ref = "H8", f"A7:T{max(last, 7)}"
        if items:
            ws.conditional_formatting.add(f"H8:H{last}", FormulaRule(formula=['H8="未匹配"'], fill=PatternFill("solid", fgColor="FDECEA")))
            ws.conditional_formatting.add(f"H8:H{last}", FormulaRule(formula=['H8="待复核"'], fill=PatternFill("solid", fgColor="FFF4D6")))
            limit = float(p["threshold"]) / 100
            ws.conditional_formatting.add(f"Q8:Q{last}", CellIsRule(operator="greaterThanOrEqual", formula=[str(limit)], fill=PatternFill("solid", fgColor="FDECEA")))
            ws.conditional_formatting.add(f"Q8:Q{last}", CellIsRule(operator="lessThanOrEqual", formula=[str(-limit)], fill=PatternFill("solid", fgColor="E7F5ED")))
        widths = [8, 14, 8, 26, 22, 9, 12, 11, 26, 22, 14, 13, 13, 15, 15, 15, 12, 11, 24, 24]
        for col, width in enumerate(widths, 1): ws.column_dimensions[get_column_letter(col)].width = width
        raw = wb.create_sheet("原始清单")
        raw.sheet_view.showGridLines = False
        raw_headers = ["序号", "源工作表", "源行", "材料名称", "规格型号", "单位", "数量", "备注"]
        for col, value in enumerate(raw_headers, 1): raw.cell(2, col, value)
        self.header(raw, 2, len(raw_headers))
        for index, item in enumerate(items, 1):
            values = [index, item["source_sheet"], item["source_row"], item["name"], item["spec"], item["unit"], item["quantity"], item["remark"]]
            for col, value in enumerate(values, 1): raw.cell(index + 2, col, value)
            raw.cell(index + 2, 7).number_format = "#,##0.00"
        raw.freeze_panes, raw.auto_filter.ref = "A3", f"A2:H{max(2, len(items) + 2)}"
        for col, width in enumerate([8, 14, 8, 28, 24, 10, 14, 32], 1): raw.column_dimensions[get_column_letter(col)].width = width
        try:
            wb.calculation.fullCalcOnLoad = wb.calculation.forceFullCalc = True
        except AttributeError:
            pass
        filename = re.sub(r"[\\/:*?\"<>|]", "_", p["name"]) + "_调差结果.xlsx"
        return self.bytes(wb), filename
