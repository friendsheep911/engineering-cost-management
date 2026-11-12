# -*- coding: utf-8 -*-
"""把指定期次的信息价数据导出为 Excel 工作簿（多 Sheet）。

用法:
    python tools/export_issue_xlsx.py 2026年第4期
    python tools/export_issue_xlsx.py            # 缺省导出最新一期

输出:
    exports/青海工程造价管理信息_<期次>.xlsx

Sheet 结构:
    说明          —— 期次元信息 + 各表统计
    数据总表      —— 全部表格平铺长表（一行 = 一个材料×一个计价区域）
    西宁/海东/... —— 按地区分 Sheet，材料/干混砂浆/劳务/机械台班 分区块宽表
"""
import json
import os
import sys
from collections import OrderedDict

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "exports")

KIND_CN = {
    "material": "建设工程部分材料指导价",
    "mortar": "干混砂浆市场参考价",
    "labor": "建设工程劳务市场信息指导价",
    "machine": "施工机械台班市场租赁价",
    "seedling": "园林绿化苗木行业指导价",
}
KIND_ORDER = ["material", "mortar", "labor", "machine", "seedling"]
KIND_TAG = {"material": "材料", "mortar": "干混砂浆", "labor": "劳务", "machine": "机械台班", "seedling": "苗木"}

# ---------- 样式 ----------
F_TITLE = Font(name="微软雅黑", size=14, bold=True, color="1F3864")
F_SUB = Font(name="微软雅黑", size=9, color="595959")
F_BLOCK = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
F_HEAD = Font(name="微软雅黑", size=10, bold=True, color="1F3864")
F_CELL = Font(name="微软雅黑", size=10)
F_NOTE = Font(name="微软雅黑", size=9, color="808080")
FILL_BLOCK = PatternFill("solid", fgColor="4472C4")
FILL_HEAD = PatternFill("solid", fgColor="D9E2F3")
FILL_ALT = PatternFill("solid", fgColor="F2F5FB")
THIN = Side(style="thin", color="B4C6E7")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
AL_C = Alignment(horizontal="center", vertical="center", wrap_text=True)
AL_L = Alignment(horizontal="left", vertical="center", wrap_text=True)
AL_R = Alignment(horizontal="right", vertical="center")


def list_issues():
    """返回 data 目录下所有期次键（按年月期排序）。"""
    out = []
    for fn in os.listdir(DATA_DIR):
        if fn.endswith(".json") and "年第" in fn and fn.startswith("20"):
            out.append(fn[:-5])
    def key(k):
        m = k.replace("年第", " ").replace("期", "")
        y, n = m.split()
        return (int(y), int(n))
    return sorted(out, key=key)


def load(issue):
    with open(os.path.join(DATA_DIR, issue + ".json"), encoding="utf-8") as f:
        return json.load(f)


def cell_val(p, key):
    v = (p or {}).get(key)
    if not v:
        return None
    if v.get("num") is not None:
        return v["num"]
    return v.get("raw")


def region_cols(sec):
    """section 内的计价区域列（按首次出现顺序）。"""
    cols = OrderedDict()
    for r in sec.get("rows", []):
        for p in r.get("prices", []):
            cols.setdefault(p["area"], None)
    return list(cols.keys())


def has_excl(sec):
    for r in sec.get("rows", []):
        for p in r.get("prices", []):
            if p.get("excl"):
                return True
    return False


def kind_sections(doc):
    """按 kind 顺序返回 [(kind, [sec,...]), ...]。"""
    buckets = OrderedDict((k, []) for k in KIND_ORDER)
    for s in doc["sections"]:
        buckets.setdefault(s.get("kind"), []).append(s)
    return [(k, v) for k, v in buckets.items() if v]


def short_region(sec):
    return (sec.get("region") or "?").replace("地区", "").replace("州", "")


# ================= Sheet: 说明 =================
def sheet_readme(wb, doc, issue, stats):
    ws = wb.create_sheet("说明")
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 62
    meta = doc["meta"]
    rows = [
        ("刊物名称", meta.get("title")),
        ("期次", f'{meta.get("period")}（{meta.get("issue")}）'),
        ("发布日期", meta.get("publishDate")),
        ("发布单位", meta.get("publisher")),
        ("源文件", meta.get("sourceFile")),
        ("源文件页数", meta.get("sourcePages")),
        ("数据抽取时间", meta.get("extractedAt")),
        ("导出时间", stats["exported_at"]),
        ("", ""),
        ("包含地区", "、".join(stats["regions"])),
        ("包含表格", f'{stats["n_sec"]} 张（材料 {stats["n_mat"]} / 干混砂浆 {stats["n_mortar"]} / 劳务 {stats["n_labor"]} / 机械台班 {stats["n_machine"]}）'),
        ("数据行合计", f'{stats["n_rows"]} 行（按“材料×计价区域”展开为 {stats["n_long"]} 条价格记录）'),
        ("", ""),
        ("Sheet 说明", "数据总表 = 全部表格平铺长表，便于筛选/透视"),
        ("", "各地区 Sheet = 按地区分区块宽表，材料按计价区域横排含税/除税价"),
        ("", "价格单位：材料多为 元/t 等（见“单位”列），机械台班 元/台班，劳务 元/工日"),
        ("", "含税/除税：材料、机械台班含两列；劳务、部分表格仅含税或单一价格"),
        ("", "机械台班价格存在区间值（如 600-700），此时单元格为文本"),
    ]
    ws["A1"] = f'青海工程造价管理信息 {meta.get("period")}（{meta.get("issue")}）信息价数据'
    ws["A1"].font = F_TITLE
    ws.merge_cells("A1:B1")
    r = 3
    for k, v in rows:
        ws.cell(row=r, column=1, value=k).font = F_HEAD
        c = ws.cell(row=r, column=2, value=v)
        c.font = F_CELL
        c.alignment = AL_L
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="各表明细").font = F_TITLE
    r += 1
    heads = ["序号", "地区", "类别", "表格名称", "行数", "计价区域"]
    for j, h in enumerate(heads, 1):
        c = ws.cell(row=r, column=j, value=h)
        c.font = F_HEAD
        c.fill = FILL_HEAD
        c.alignment = AL_C
        c.border = BORDER
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 46
    ws.column_dimensions["E"].width = 8
    ws.column_dimensions["F"].width = 42
    r += 1
    for i, s in enumerate(doc["sections"], 1):
        vals = [i, s.get("region"), KIND_TAG.get(s.get("kind"), s.get("kind")),
                s.get("label") or s.get("title"), s.get("itemCount"), "、".join(s.get("areas") or [])]
        for j, v in enumerate(vals, 1):
            c = ws.cell(row=r, column=j, value=v)
            c.font = F_CELL
            c.alignment = AL_C if j in (1, 3, 5) else AL_L
            c.border = BORDER
        r += 1
    ws.freeze_panes = "A3"
    return ws


# ================= Sheet: 数据总表 =================
def sheet_long(wb, doc, issue):
    ws = wb.create_sheet("数据总表")
    heads = ["期次", "地区", "类别", "表格名称", "分组", "名称", "规格型号", "单位",
             "计价区域", "含税价", "除税价", "备注"]
    widths = [12, 8, 10, 28, 20, 22, 24, 8, 12, 12, 12, 26]
    for j, (h, w) in enumerate(zip(heads, widths), 1):
        c = ws.cell(row=1, column=j, value=h)
        c.font = F_HEAD
        c.fill = FILL_HEAD
        c.alignment = AL_C
        c.border = BORDER
        ws.column_dimensions[get_column_letter(j)].width = w
    r = 2
    n = 0
    for s in doc["sections"]:
        label = s.get("label") or s.get("title")
        for row in s.get("rows", []):
            for p in row.get("prices", []):
                vals = [issue, s.get("region"), KIND_TAG.get(s.get("kind"), s.get("kind")),
                        label, row.get("group") or "", row.get("name") or "",
                        row.get("spec") or "", row.get("unit") or "",
                        p.get("area") or "", cell_val(p, "incl"), cell_val(p, "excl"),
                        row.get("note") or ""]
                for j, v in enumerate(vals, 1):
                    c = ws.cell(row=r, column=j, value=v)
                    c.font = F_CELL
                    c.border = BORDER
                    c.alignment = AL_R if j in (10, 11) else (AL_C if j in (1, 2, 3, 8, 9) else AL_L)
                if n % 2:
                    for j in range(1, len(heads) + 1):
                        ws.cell(row=r, column=j).fill = FILL_ALT
                r += 1
                n += 1
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(heads))}{r - 1}"
    return ws, n


# ================= Sheet: 各地区宽表 =================
def sheet_region(wb, doc, issue, region, secs):
    ws = wb.create_sheet(region)
    meta = doc["meta"]
    reg_disp = region if region.endswith(("协会", "市", "州")) else region + "地区"
    ws["A1"] = f'{reg_disp} 信息价（{meta.get("period")}　{meta.get("issue")}）'
    ws["A1"].font = F_TITLE
    ws["A2"] = f'源文件 {meta.get("sourceFile")}　数据抽取 {meta.get("extractedAt")}'
    ws["A2"].font = F_SUB
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 14
    r = 4
    for bi, s in enumerate(secs, 1):
        kind = s.get("kind")
        cols = region_cols(s)
        dual = has_excl(s) and len(cols) > 0
        if kind == "labor":
            dual = False
        # 列头
        heads = ["序号", "类别/分组", "名称", "规格型号", "单位"]
        if dual:
            for a in cols:
                heads += [f"{a} 含税", f"{a} 除税"]
        else:
            heads += list(cols)
        heads += ["备注"]
        ncol = len(heads)
        # 区块标题
        bt = f'{bi}、{KIND_CN.get(kind, kind)}（{s.get("itemCount")} 项）'
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
        c = ws.cell(row=r, column=1, value=bt)
        c.font = F_BLOCK
        c.fill = FILL_BLOCK
        c.alignment = AL_L
        for j in range(1, ncol + 1):
            ws.cell(row=r, column=j).border = BORDER
        r += 1
        for j, h in enumerate(heads, 1):
            c = ws.cell(row=r, column=j, value=h)
            c.font = F_HEAD
            c.fill = FILL_HEAD
            c.alignment = AL_C
            c.border = BORDER
        r += 1
        for i, row in enumerate(s.get("rows", []), 1):
            pmap = {p["area"]: p for p in row.get("prices", [])}
            vals = [i, row.get("group") or "", row.get("name") or "",
                    row.get("spec") or "", row.get("unit") or ""]
            if dual:
                for a in cols:
                    p = pmap.get(a)
                    vals += [cell_val(p, "incl"), cell_val(p, "excl")]
            else:
                for a in cols:
                    p = pmap.get(a)
                    vals.append(cell_val(p, "incl"))
            vals.append(row.get("note") or "")
            for j, v in enumerate(vals, 1):
                c = ws.cell(row=r, column=j, value=v)
                c.font = F_CELL
                c.border = BORDER
                if j == 1:
                    c.alignment = AL_C
                elif j in (2, 3, 4, 5):
                    c.alignment = AL_L
                elif j == ncol:
                    c.alignment = AL_L
                else:
                    c.alignment = AL_R
                if i % 2 == 0:
                    c.fill = FILL_ALT
            r += 1
        # 表注
        for note in (s.get("notes") or []):
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
            c = ws.cell(row=r, column=1, value=note)
            c.font = F_NOTE
            c.alignment = AL_L
            r += 1
        r += 1
    # 列宽
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 24
    ws.column_dimensions["D"].width = 22
    ws.column_dimensions["E"].width = 7
    for j in range(6, ncol):
        ws.column_dimensions[get_column_letter(j)].width = 12
    ws.column_dimensions[get_column_letter(ncol)].width = 30
    ws.freeze_panes = "A4"
    return ws


# ================= main =================
def main():
    issues = list_issues()
    issue = sys.argv[1] if len(sys.argv) > 1 else issues[-1]
    if issue not in issues:
        print("期次不存在:", issue, "\n可选:", ", ".join(issues[-6:]))
        sys.exit(1)
    doc = load(issue)
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        pass
    except Exception:
        pass
    import datetime
    stats = {
        "exported_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_sec": len(doc["sections"]),
        "n_mat": sum(1 for s in doc["sections"] if s.get("kind") == "material"),
        "n_mortar": sum(1 for s in doc["sections"] if s.get("kind") == "mortar"),
        "n_labor": sum(1 for s in doc["sections"] if s.get("kind") == "labor"),
        "n_machine": sum(1 for s in doc["sections"] if s.get("kind") == "machine"),
        "n_rows": sum(len(s.get("rows", [])) for s in doc["sections"]),
        "regions": [],
    }
    wb = Workbook()
    wb.remove(wb.active)

    # 按地区归并 sections（保持首次出现顺序）
    reg_order = OrderedDict()
    for s in doc["sections"]:
        reg_order.setdefault(short_region(s), []).append(s)
    stats["regions"] = list(reg_order.keys())

    n_long = sheet_long(wb, doc, issue)[1]
    stats["n_long"] = n_long
    for reg, secs in reg_order.items():
        sheet_region(wb, doc, issue, reg, secs)
    # 说明放最前
    ws = sheet_readme(wb, doc, issue, stats)
    wb._sheets.remove(ws)
    wb._sheets.insert(0, ws)

    out = os.path.join(OUT_DIR, f'青海工程造价管理信息_{issue}.xlsx')
    wb.save(out)
    print("OK ->", out)
    print(f'  期次 {issue} | sections {stats["n_sec"]} | 汇总行 {stats["n_rows"]} | 长表记录 {n_long}')
    print("  地区:", "、".join(stats["regions"]))
    return out


if __name__ == "__main__":
    main()
