# -*- coding: utf-8 -*-
"""
青海工程造价管理信息 —— PDF 结构化抽取器 (v3)
=================================================
把《青海工程造价管理信息》PDF 中的价格表抽取为结构化 JSON，供前端渲染与查询。

本刊排版特点（决定了抽取策略）
------------------------------
1. 【列块拆分】一张逻辑表常被拆到两页：一页含 "材料名称/型号规格/单位 + 前2个县"，
   下一页只有 "后几个县" 的价格且无名称列。两页行数严格相等 →
   按【行索引】把 B 块价格合并回 A 块，并用 "行数相等" 做硬校验。
2. 【表题只在附件首页出现】，续页无表题。→ 用"X地区"标记 + 表格列头判定地区与种类，
   不做表题传递。
3. 【多行表头】有的表 r0 是标题行(r1 才是真表头)，有的是 r0/r1 双层(县名 + 含税/除税)。
4. 单元格含空格、换行、上下标 m³ → 统一清洗。

用法：
    python extract_info_price.py <PDF> [--out 目录] [--start-page 18]

输出：
    <out>/<期号>.json / .js     结构化数据（.js 便于 file:// 直接打开）
    <out>/index.json / index.js 期数索引（前端据此生成"期数"下拉）
    <out>/_report.txt           抽取校验报告
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# 优先使用项目自带 vendor 目录中的依赖（使整个系统拷走即可用）
_VENDOR = Path(__file__).resolve().parent.parent / "vendor"
if _VENDOR.exists() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

try:
    import legacy_layout as LEGACY
except ImportError:
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parent))
    import legacy_layout as LEGACY

import pymupdf

REGION_RE = re.compile(r"(西宁|海东|海西|黄南|海北|果洛|海南|玉树)地区")
NOTE_RE = re.compile(r"^\s*(注|备注|说明)\s*[:：]")
NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")

TYPE_LABEL = {
    "material": "建设工程部分材料指导价",
    "mortar": "干混砂浆市场参考价",
    "green": "绿色建筑材料指导价",
    "labor": "建设工程劳务市场信息指导价",
    "machine": "施工机械台班市场租赁价",
}

NAME_KW = ("材料名称", "名称", "工种名称", "机械名称")
SPEC_KW = ("型号", "规格", "强度等级")


# ------------------------------------------------------------------ 清洗

def clean(s):
    if s is None:
        return ""
    t = str(s)
    t = t.replace("m\n3", "m³").replace("m\n2", "m²").replace("m\n³", "m³").replace("m\n²", "m²")
    t = t.replace("\n", "").replace(" ", "").replace("\u3000", "")
    t = t.replace("（", "(").replace("）", ")")
    return t.strip()


def parse_price(s):
    raw = clean(s)
    if not raw or raw in ("-", "—", "/"):
        return None
    if "含税" in raw or "除税" in raw:      # 表头残字，非价格
        return None
    if NUM_RE.match(raw):
        try:
            return {"raw": raw, "num": float(raw.replace(",", ""))}
        except ValueError:
            pass
    return {"raw": raw}


def norm_unit(s):
    return clean(s).replace("m3", "m³").replace("m2", "m²")


# ------------------------------------------------------------------ 表头解析

def is_area_row(r):
    """判定一行是否为"地区名行"：前两列（序号/名称）为空，且有非空非数值单元格。
    典型形态：['', '', '', '德令哈', '乌兰', ...]（跟在真表头之后）。"""
    if not r or len(r) < 3:
        return False
    if r[0] or r[1]:
        return False
    if any("含税" in c or "除税" in c for c in r):
        return False
    vals = [c for c in r[2:] if c]
    return bool(vals) and not any(NUM_RE.match(v) for v in vals)


def split_header(rows):
    """返回 (header_rows, body_start)。区分三种形态：
       A. 单层表头            -> [r0]
       B. 双层：县名 + 含税/除税 -> [r0, r1]
       C. 标题行 + 表头 (+地区名行) -> [r1] 或 [r1, 地区名行]"""
    if len(rows) < 2:
        return rows[:1], 1
    r0 = [clean(c) for c in rows[0]]
    r1 = [clean(c) for c in rows[1]]
    n0 = sum(1 for c in r0 if c)
    r1_has_price = any("含税" in c or "除税" in c for c in r1)

    if r1_has_price and n0 <= 1:
        hdr, start = [r1], 2                # 标题行 + 真表头（机械类）
    elif r1_has_price:
        return [r0, r1], 2                  # 双层：县名 + 含税/除税
    elif n0 <= 1 and any(k in "".join(r1[:3]) for k in ("序号", "名称")):
        hdr, start = [r1], 2                # 标题行 + 真表头（劳务类）
    elif any(k in "".join(r1[:3]) for k in ("序号", "名称")) and not any(k in "".join(r0[:3]) for k in ("序号", "名称")):
        hdr, start = [r1], 2
    else:
        return [r0], 1

    # 表头之后若紧跟"含税/除税行"或"地区名行"，则吸收进表头块（最多两行，如
    # 多地区机械表：表头 + 地区名行 + 含税/除税行）
    extra = 0
    while start < len(rows) and extra < 2:
        r2 = [clean(c) for c in rows[start]]
        if any(r2) and any("含税" in c or "除税" in c for c in r2):
            hdr.append(r2); start += 1; extra += 1      # 含税/除税行
        elif is_area_row(r2):
            hdr.append(r2); start += 1; extra += 1      # 地区名行
        else:
            break
    return hdr, start


def build_cols(header_rows, tail_rows):
    """
    构造列定义。返回 (cols, areas, body_start_extra)
    col = {'head':.., 'area':..|None, 'price':'incl'|'excl'|None, 'role':..}
    """
    r0 = [clean(c) for c in header_rows[0]]
    r1 = [clean(c) for c in header_rows[1]] if len(header_rows) > 1 else []
    r2 = [clean(c) for c in header_rows[2]] if len(header_rows) > 2 else []
    has_sub = bool(r1) and any(("含税" in c or "除税" in c) for c in r1)
    has_sub2 = bool(r2) and any(("含税" in c or "除税" in c) for c in r2)
    has_area = bool(r1) and not has_sub and is_area_row(r1)

    cols = []
    if has_sub:
        cur = ""
        for i, c in enumerate(r0):
            if c:
                cur = c
            sub = r1[i] if i < len(r1) else ""
            p = "incl" if "含税" in sub else ("excl" if "除税" in sub else None)
            cols.append({"head": c or sub, "area": cur if p else None, "price": p})
    elif has_area and has_sub2:
        # 表头 + 地区名行 + 含税/除税行（多地区机械表：每县下各有含税/除税两列）
        # 注意县名为合并单元格，仅出现在两列中的第一列，需向后继承
        cur = ""
        for i, c in enumerate(r0):
            a = r1[i] if i < len(r1) else ""
            if a and not NUM_RE.match(a):
                cur = a
            sub = r2[i] if i < len(r2) else ""
            p = "incl" if "含税" in sub else ("excl" if "除税" in sub else None)
            if p:
                cols.append({"head": c or cur, "area": cur, "price": p})
            else:
                cols.append({"head": c, "area": None, "price": None})
    elif has_area:
        # 表头 + 地区名行：地区名列即为单价格列（劳务/多地区单价表）
        for i, c in enumerate(r0):
            area = r1[i] if i < len(r1) else ""
            if area and not NUM_RE.match(area):
                cols.append({"head": c or area, "area": area, "price": "incl"})
            else:
                cols.append({"head": c, "area": None, "price": None})
    else:
        for c in r0:
            p = "incl" if "含税" in c else ("excl" if "除税" in c else None)
            cols.append({"head": c, "area": None, "price": p})

    # 若无含税/除税列但存在"价格/单价"列（劳务人工工资单价）
    if not any(c["price"] for c in cols):
        for c in cols:
            if "价格" in c["head"] or "单价" in c["head"]:
                c["price"] = "incl"

    # 列角色
    for i, c in enumerate(cols):
        h = c["head"]
        if c["price"]:
            c["role"] = "price"
        elif "序号" in h:
            c["role"] = "idx"
        elif any(k in h for k in NAME_KW):
            c["role"] = "name"
        elif any(k in h for k in SPEC_KW):
            c["role"] = "spec"
        elif "单位" in h:
            c["role"] = "unit"
        elif "备注" in h:
            c["role"] = "note"
        else:
            c["role"] = None

    # 兜底：没有识别出 name 列时，取第一个非价格、非序号列
    if not any(c["role"] == "name" for c in cols):
        for c in cols:
            if c["role"] not in ("price", "idx"):
                c["role"] = "name"
                break

    areas = []
    for c in cols:
        if c["price"] == "incl" and c["area"] and c["area"] not in areas:
            areas.append(c["area"])
    return cols, areas


def header_kind(rows):
    """判定表种类，返回 (kind, has_name_column)。
    关键字在 rows[:3] 全文中查找（标题行可能含"人工成本/台班"等关键信息），
    名称列判定则同时看前两行（双层表头名称在 r0，标题行+表头时在 r1）。"""
    if not rows:
        return None, False
    hdr, _ = split_header(rows)
    txt = "".join(clean(c) for r in rows[:3] for c in r)
    h3 = "".join(clean(c) for c in rows[0][:3])
    h3b = "".join(clean(c) for c in rows[1][:3]) if len(rows) > 1 else ""
    has_name = any(k in h3 or k in h3b for k in NAME_KW) or "序号" in h3 or "序号" in h3b
    head3 = h3 if any(k in h3 for k in NAME_KW) or "序号" in h3 else h3b

    if "强度等级" in txt:
        return "mortar", has_name
    if "机械名称" in txt or ("台班" in txt and "序号" in txt):
        return "machine", has_name
    if "工种名称" in txt or "人工成本" in txt:
        return "labor", has_name
    if "消耗量" in txt or "项目名称" in txt:
        return "indicator", has_name
    if has_name and ("含税" in txt or "除税" in txt or "价格" in txt or "单价" in txt):
        return "material", True
    return None, has_name


# ------------------------------------------------------------------ 行解析

def parse_rows(rows, cols, body_start, default_area):
    """返回 (items, group_names)。item 带 _ri = 行在 body 中的索引，用于 A/B 精确对齐。"""
    body = rows[body_start:]
    price_idx = [i for i, c in enumerate(cols) if c["price"]]
    role_idx = {c["role"]: i for i, c in enumerate(cols) if c["role"] in ("name", "spec", "unit", "note")}
    n_i, s_i, u_i, o_i = (role_idx.get("name"), role_idx.get("spec"),
                          role_idx.get("unit"), role_idx.get("note"))

    items, groups, cur_group, cur_name = [], [], "", ""

    for ri, r in enumerate(body):
        cells = [clean(c) for c in r]
        if len(cells) < len(cols):
            cells += [""] * (len(cols) - len(cells))
        if not any(cells):
            continue
        # 分组标题行：首列有值、其余全空
        if cells[0] and not any(cells[i] for i in range(1, len(cols))):
            cur_group = cells[0]
            if cur_group not in groups:
                groups.append(cur_group)
            continue

        if n_i is not None and n_i < len(cells) and cells[n_i] and not NUM_RE.match(cells[n_i]):
            cur_name = cells[n_i]

        prices = []
        i = 0
        while i < len(cols):
            c = cols[i]
            if c["price"] == "incl" and i + 1 < len(cols) and cols[i + 1]["price"] == "excl":
                inc, exc = parse_price(cells[i]), parse_price(cells[i + 1])
                if inc or exc:
                    prices.append({"area": c["area"] or default_area, "incl": inc, "excl": exc})
                i += 2
                continue
            if c["price"] == "incl":
                inc = parse_price(cells[i])
                if inc:
                    prices.append({"area": c["area"] or default_area, "incl": inc, "excl": None})
            i += 1

        spec = cells[s_i] if s_i is not None and s_i < len(cells) else ""
        unit = norm_unit(cells[u_i]) if u_i is not None and u_i < len(cells) else ""
        note = cells[o_i] if o_i is not None and o_i < len(cells) else ""
        if not cur_name and not spec and not prices:
            continue

        items.append({"_ri": ri, "name": cur_name, "spec": spec, "unit": unit,
                      "prices": prices, "group": cur_group, "note": note})

    return items, groups


def page_note(page):
    # 遇到表标题（（一）/（二）…）或表头行即停止，避免把表格文字吸进备注
    stop_re = re.compile(r"（[一二三四五六七八九十]+）|序\s*号|名称|单位")
    out, grab = [], False
    for ln in page.get_text().splitlines():
        s = ln.strip()
        if NOTE_RE.match(s):
            grab = True
        elif grab and (REGION_RE.search(s) or stop_re.search(s)):
            break
        if grab and s:
            out.append(s)
    t = " ".join(out)
    return t if len(t) > 4 else ""


def pick_price_cells(cols):
    """返回 [(area, i_incl, i_excl_or_None)]"""
    out, i = [], 0
    while i < len(cols):
        c = cols[i]
        if c["price"] == "incl" and i + 1 < len(cols) and cols[i + 1]["price"] == "excl":
            out.append((c["area"], i, i + 1))
            i += 2
            continue
        if c["price"] == "incl":
            out.append((c["area"], i, None))
            i += 1
            continue
        i += 1
    return out


# ------------------------------------------------------------------ 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", default=None)
    ap.add_argument("--start-page", type=int, default=0,
                    help="价格表起始页（1 起）。缺省 0 = 自动探测（推荐）")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    doc = pymupdf.open(pdf_path)
    out_dir = Path(args.out) if args.out else pdf_path.parent / "parsed"
    issues = []

    # ---------- 元信息
    front = "".join(doc[i].get_text() for i in range(min(18, doc.page_count)))
    mi = re.search(r"总\s*第\s*(\d+)\s*期", front)
    mp = re.search(r"(\d{4})\s*年\s*(\d+)\s*[-—~]\s*(\d+)\s*月", front)
    mq = re.search(r"第(.+?)期", front)
    md_ = re.findall(r"(\d{4})\s*年\s*(\d+)\s*月\s*(\d+)\s*日", front)
    pub = f"{md_[-1][0]}-{int(md_[-1][1]):02d}-{int(md_[-1][2]):02d}" if md_ else ""
    period = f"{mp.group(1)}年{mp.group(2)}-{mp.group(3)}月" if mp else ""
    period_no = mq.group(1).strip() if mq else ""
    issue_no = mi.group(1) if mi else "?"
    issue_key = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", f"{period}-第{period_no}期")

    # 期号权威来源：官网期次清单（优先于封面解析，统一为「YYYY年第N期」）
    # 新版双月刊封面写作「2024年5-6月」，会被误当作期号；官网清单才是规范期号。
    off_issue = off_no = off_date = ""
    _off_path = out_dir / "official_issues.json"
    if _off_path.exists():
        try:
            _off_list = json.loads(_off_path.read_text(encoding="utf-8")).get("issues", [])
            _mfn = re.search(r"(\d{4})\s*年\s*第\s*(\d+)\s*期", pdf_path.name)
            for _i, _o in enumerate(_off_list):
                hit = _o.get("attach", "").endswith(pdf_path.name)   # 按附件名精确匹配
                if not hit and _mfn:                                 # 按文件名期号匹配
                    hit = _o.get("issue") == f"{_mfn.group(1)}年第{int(_mfn.group(2))}期"
                if hit:
                    off_issue = _o["issue"]
                    off_no = str(131 + _i + 1)                       # 2020年第1期 = 总第132期
                    off_date = _o.get("published", "")
                    break
        except Exception:
            pass

    if off_issue:
        period = off_issue                                            # 规范期号，如 2024年第3期
        _m2 = re.search(r"第\s*(\d+)\s*期", off_issue)
        period_no = _m2.group(1) if _m2 else period_no
        issue_no = off_no or issue_no
        issue_key = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", off_issue)
        if off_date and not pub:
            pub = off_date
    else:
        # 兜底 1：封面解析不到年月时，用 PDF 文件名（…_2020年第1期.pdf）
        if not period:
            mf = re.search(r"(\d{4})\s*年\s*第\s*(\d+)\s*期", pdf_path.name)
            if mf:
                period = f"{mf.group(1)}年第{mf.group(2)}期"
                period_no = mf.group(2)
                issue_key = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", period)

    meta = {"title": "青海工程造价管理信息", "issue": f"总第{issue_no}期",
            "issueNo": int(issue_no) if issue_no.isdigit() else issue_no,
            "period": period, "periodNo": period_no, "publishDate": pub,
            "publisher": "青海省建设工程造价站", "sourceFile": pdf_path.name,
            "sourcePages": doc.page_count,
            "extractedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    # ---------- 1. 逐页收集表格
    raw, cur_region = [], ""
    legacy_seq = []
    logic = []
    attach_re = re.compile(r"附件\s*\d|价格指数|造价指标|工程造价指标|主要结构做法|消耗量指标|工程概况|指标分析|钢材价格走势")

    def legacy_kind(text, hint):
        if "绿色建筑材料" in text:
            return "green"
        if "干混砂浆" in text:
            return "mortar"
        if "劳务" in text:
            return "labor"
        if "机械台班" in text:
            return "machine"
        return "material"

    # ---------- 0. 自动探测价格表起始页
    # 旧刊（2020~2023）指导价从第 6 页左右就开始，不能固定从第 18 页起，否则开头几页
    # （通常是西宁的材料表）会被整段漏掉。
    start_page = args.start_page
    if not start_page:
        start_page = 1
        for i in range(doc.page_count):
            txt = doc[i].get_text()
            if attach_re.search(txt):
                continue
            try:
                tabs = doc[i].find_tables().tables
            except Exception:
                tabs = []
            hit = False
            for t in tabs:
                d = t.extract() or []
                if len(d) < 2:
                    continue
                # 压缩表只抽成 2 行，表头就是第 1 行，所以不能按行数判断
                head = "".join(str(c or "") for c in d[0])
                if any(k in head for k in ("材料名称", "工种名称", "机械名称", "项目名称")):
                    hit = True
                    break
            if hit:
                start_page = i + 1
                break
        if os.environ.get("XP_DEBUG"):
            print(f"[DBG] 自动起始页 = {start_page}")

    for pno in range(start_page - 1, doc.page_count):
        page = doc[pno]
        text = page.get_text()
        if attach_re.search(text):
            continue
        rm = REGION_RE.search(text)
        if rm:
            cur_region = rm.group(1) + "地区"
        hint = "green" if "绿色建筑材料" in text else ("mortar" if "干混砂浆" in text else "none")
        for tb in page.find_tables().tables:
            d = tb.extract()
            if not d or len(d) < 2:
                if os.environ.get("XP_DEBUG"):
                    print(f"[DBG] p{pno+1} 表行数不足跳过 rows={len(d) if d else 0}")
                continue
            # --- 旧版式 1：压缩单元格（2020~2022 横排刊物）
            if LEGACY.is_compressed(d):
                parsed = LEGACY.parse_compressed(page, tb)
                if parsed:
                    has_name = any(c["role"] == "name" for c in parsed["cols"])
                    legacy_seq.append({"pno": pno + 1, "region": cur_region or "未识别",
                                       "kind": legacy_kind(text, hint),
                                       "type": "compA" if has_name else "compB",
                                       "parsed": parsed})
                    continue
            # --- 旧版式 2：网格横排 A/B（2023-6 等，表头即县名行）
            r0txt = "".join(clean(c) for c in d[0])
            if ("名称" not in r0txt and "序号" not in r0txt and "含税" not in r0txt
                    and "除税" not in r0txt):
                cnty = [clean(c) for c in d[0] if clean(c)]
                if len(cnty) >= 3 and not any(NUM_RE.match(c) for c in cnty):
                    k, has_name = header_kind(d)
                    legacy_seq.append({"pno": pno + 1, "region": cur_region or "未识别",
                                       "kind": legacy_kind(text, hint),
                                       "type": "gridA" if has_name else "gridB",
                                       "rows": d})
                    continue
            # --- 旧版式 2b：网格横排 A 页（名称列 + 2 个以上县名价格列，无含税/除税）
            # 注意排除新式双层表头（r0=县名行、r1=含税/除税行）
            k0, has_name0 = header_kind(d)
            r1txt = "".join(clean(c) for c in d[1]) if len(d) > 1 else ""
            if (has_name0 and "含税" not in r0txt and "除税" not in r0txt
                    and "含税" not in r1txt and "除税" not in r1txt):
                tail = [clean(c) for c in d[0][3:] if clean(c)]
                if len(tail) >= 2 and not any(NUM_RE.match(c) for c in tail):
                    legacy_seq.append({"pno": pno + 1, "region": cur_region or "未识别",
                                       "kind": legacy_kind(text, hint),
                                       "type": "gridA", "rows": d})
                    continue
            k, has_name = header_kind(d)
            hdr, bstart = split_header(d)
            cols, areas = build_cols(hdr, d[bstart:])
            raw.append({"page": pno + 1, "region": cur_region or "未识别", "hint": hint,
                        "rows": d, "kind": k, "hasName": has_name, "bodyStart": bstart,
                        "cols": cols, "areas": areas, "B": None})

    # ---------- 1b. 开头未识别的表归西宁地区（旧刊西宁表在最前、标题在前一页被跳过）
    first_region_pno = min([t["page"] for t in raw if t["region"] != "未识别"]
                           + [t["pno"] for t in legacy_seq if t["region"] != "未识别"]
                           or [10 ** 9])
    for t in raw:
        if t["region"] == "未识别" and t["page"] < first_region_pno:
            t["region"] = "西宁地区"
    for t in legacy_seq:
        if t["region"] == "未识别" and t["pno"] < first_region_pno:
            t["region"] = "西宁地区"

    # ---------- 2L. 旧版式横向 A/B 配对 -> 逻辑表
    def legacy_to_items(merged, default_area):
        cols, rows = merged["cols"], merged["rows"]
        items = []
        cur_name = ""
        for r in rows:
            cells = {c["role"]: (r[i] if i < len(r) else "") for i, c in enumerate(cols)}
            prices = []
            for i, c in enumerate(cols):
                if c["role"] != "price" or i >= len(r) or not r[i]:
                    continue
                p = parse_price(r[i])
                if not p:
                    continue
                area = c["area"] or default_area
                if c["ptype"] == "incl":
                    same = next((q for q in prices if q["area"] == area and q["incl"] is None), None)
                    if same:
                        same["incl"] = p
                    else:
                        prices.append({"area": area, "incl": p, "excl": None})
                else:
                    same = next((q for q in prices if q["area"] == area and q["excl"] is None), None)
                    if same:
                        same["excl"] = p
                    else:
                        prices.append({"area": area, "incl": None, "excl": p})
            nm = cells.get("name") or ""
            if nm and not NUM_RE.match(nm):
                cur_name = nm
            if not cur_name and not prices:
                continue
            items.append({"name": cur_name, "spec": cells.get("spec") or "",
                          "unit": norm_unit(cells.get("unit") or ""),
                          "prices": prices, "group": "", "note": cells.get("note") or ""})
        return items

    def legacy_emit(reg, a, b, merged):
        if isinstance(merged, tuple):
            cols, rows = merged
            merged = {"cols": cols, "rows": rows}
        items = legacy_to_items(merged, reg)
        if not items:
            return False
        areas = list(dict.fromkeys(
            p["area"] for it in items for p in it["prices"] if p["area"]))
        logic.append({"region": reg, "kind": a["kind"],
                      "pages": [a["pno"], b["pno"]], "areas": areas,
                      "items": items, "note": ""})
        return True

    for reg in dict.fromkeys(t["region"] for t in legacy_seq):
        seq = sorted([t for t in legacy_seq if t["region"] == reg],
                     key=lambda t: t["pno"])          # 稳定排序：同页保持表序
        used = set()
        if os.environ.get("XP_DEBUG"):
            print(f"[DBG] reg={reg} seq={[(t['pno'], t['type'], len(t.get('rows', [])) if 'rows' in t else len(t['parsed']['rows'])) for t in seq]}")

        def try_merge(a, b):
            if a["type"] == "compA":
                return LEGACY.merge_pair(a["parsed"], b["parsed"])
            return LEGACY.grid_pair(a["rows"], b["rows"])

        # 黄南/玉树式：同页或相邻页连排两张 gridA（均带名称列，县名不相交）
        for j in range(len(seq) - 1):
            t, u = seq[j], seq[j + 1]
            if (id(t) not in used and id(u) not in used
                    and t["type"] == "gridA" and u["type"] == "gridA"
                    and u["pno"] - t["pno"] <= 1
                    and len(t["rows"]) == len(u["rows"])):
                m = LEGACY.grid_pair_nn(t["rows"], u["rows"])
                if m and legacy_emit(reg, t, u, m):
                    used.add(id(t)); used.add(id(u))

        for idx, t in enumerate(seq):
            if id(t) in used or t["type"] not in ("compA", "gridA"):
                continue
            # A/B 可以前后相邻（B 可能在 A 之前，如 2020-1 海东 p39B/p40A）
            cands = sorted((u for u in seq if id(u) not in used
                            and u["type"] == t["type"][:4] + "B"
                            and abs(u["pno"] - t["pno"]) <= 3),
                           key=lambda u: (abs(u["pno"] - t["pno"]),
                                          abs(seq.index(u) - idx)))
            for u in cands:
                m = try_merge(t, u)
                if m and legacy_emit(reg, t, u, m):
                    used.add(id(t)); used.add(id(u))
                    if os.environ.get("XP_DEBUG"):
                        print(f"[DBG] 配对 p{t['pno']}({t['type']})+p{u['pno']}({u['type']}) -> 合并")
                    break
            else:
                # 无 B 的独立 A：西宁压缩单地区表 / 黄南·玉树式多县网格表
                standalone = None
                if t["type"] == "compA":
                    standalone = t["parsed"]
                elif t["type"] == "gridA":
                    gs = LEGACY.grid_standalone(t["rows"])
                    if gs:
                        standalone = {"cols": gs[0], "rows": gs[1]}
                if standalone is not None:
                    items = legacy_to_items(standalone, reg)
                    if os.environ.get("XP_DEBUG"):
                        print(f"[DBG] 独立A p{t['pno']}({t['type']}) items={len(items)}")
                    if items:
                        areas = list(dict.fromkeys(
                            p["area"] for it in items for p in it["prices"] if p["area"]))
                        logic.append({"region": reg, "kind": t["kind"],
                                      "pages": [t["pno"], t["pno"]], "areas": areas,
                                      "items": items, "note": ""})
                else:
                    issues.append(f"[需复核] {reg} p{t['pno']}：旧版式 A 页未配到 B 页")
        for t in seq:
            if id(t) not in used and t["type"] in ("compB", "gridB"):
                # 整页全是"—"的空值延续页没有数据，跳过不提示
                rowsrc = (t["parsed"]["rows"] if t["type"] == "compB" else t["rows"])
                if not any(ch.isdigit() for row in rowsrc
                           for cell in (row or []) if cell
                           for ch in str(cell)):
                    continue
                if t["type"] == "gridB":
                    # 新版式（2024-3+）B 页：r0=县名行、r1=含税/除税行、无名称列，
                    # 恰好命中旧版式 gridB 的判定而进 legacy_seq；但同表的 A 页
                    # （r0 含"材料名称"）走 raw 路径，legacy 永远配不到 gridA。
                    # 转成 raw 的"仅价格列"表插回 raw，参与 raw 的 A/B 配对
                    # （行数相等硬校验 + 按行索引合并），如 2024-3+ 海西的
                    # 都兰/乌兰/天峻/大柴旦行委、海东的民和/循化/化隆/互助等。
                    hdr, bstart = split_header(t["rows"])
                    cols, areas = build_cols(hdr, t["rows"][bstart:])
                    if areas:
                        raw.append({"page": t["pno"], "region": t["region"], "hint": "none",
                                    "rows": t["rows"], "kind": None, "hasName": False,
                                    "bodyStart": bstart, "cols": cols, "areas": areas,
                                    "B": None})
                        continue
                issues.append(f"[需复核] {reg} p{t['pno']}：旧版式 B 页无配对 A 页")

    # legacy gridB 转入 raw 后恢复页序（A/B 配对按列表序扫描）
    raw.sort(key=lambda t: t["page"])

    # ---------- 2. A/B 列块配对（B 找同名列表中立、行数相等且尚未配对者）
    for reg in dict.fromkeys(t["region"] for t in raw):
        pending = []
        for t in [x for x in raw if x["region"] == reg]:
            if t["hasName"]:
                pending.append(t)
                continue
            match = next((a for a in pending if a["B"] is None and len(a["rows"]) == len(t["rows"])), None)
            if match is not None:
                t["B"] = match
                match["B"] = t
            else:
                wait = [a for a in pending if a["B"] is None]
                why = "无待配对名称列页" if not wait else \
                      f"行数与待配对页均不等(待配对 " + \
                      ", ".join(f"p{a['page']}={len(a['rows'])}" for a in wait) + f" vs p{t['page']}={len(t['rows'])})"
                issues.append(f"[需复核] {reg} p{t['page']}：仅价格列，{why}")

    # ---------- 3. 组装逻辑表
    area_alias = {}
    for t in raw:
        if not t["hasName"]:
            continue
        if t["kind"] == "indicator":
            continue
        b = t["B"]
        kind = t["kind"]
        if t["hint"] == "green" or (b and b["hint"] == "green"):
            kind = "green"
        elif kind in (None, "material"):
            kind = "material"
        t["kind"] = kind
        if b:
            b["kind"] = kind

        items, groups = parse_rows(t["rows"], t["cols"], t["bodyStart"], t["region"])
        areas = list(t["areas"])
        merged = missing = 0

        if b:
            bprice = pick_price_cells(b["cols"])
            for a in b["areas"]:
                if a not in areas:
                    areas.append(a)
            note_i = next((i for i, c in enumerate(b["cols"]) if c["role"] == "note"), None)
            bbody = b["rows"][b["bodyStart"]:]
            by_ri = {}
            for ri, br in enumerate(bbody):
                bc = [clean(x) for x in br]
                if len(bc) < len(b["cols"]):
                    bc += [""] * (len(b["cols"]) - len(bc))
                vals, nv = [], ""
                for area, i1, i2 in bprice:
                    inc = parse_price(bc[i1]) if i1 < len(bc) else None
                    exc = parse_price(bc[i2]) if (i2 is not None and i2 < len(bc)) else None
                    if inc or exc:
                        vals.append({"area": area, "incl": inc, "excl": exc})
                if note_i is not None and note_i < len(bc):
                    nv = bc[note_i]
                if vals or nv:
                    by_ri[ri] = (vals, nv)
            for it in items:
                if it["_ri"] in by_ri:
                    vals, nv = by_ri[it["_ri"]]
                    have = {p["area"] for p in it["prices"]}
                    it["prices"].extend(v for v in vals if v["area"] not in have)
                    if nv and not it["note"]:
                        it["note"] = nv
                    merged += 1
                else:
                    missing += 1
            if missing:
                issues.append(f"[提示] {t['region']} p{t['page']}→p{b['page']}："
                              f"{len(items)} 行中 {missing} 行续页三县无报价(原刊留空)")

        logic.append({"region": t["region"], "kind": kind,
                      "pages": [t["page"], b["page"] if b else t["page"]],
                      "areas": areas, "items": items, "note": page_note(doc[t["page"] - 1])})

    # ---------- 3b. 地区别名列合并（如"格尔木"与"格尔木(唐古拉镇)"）
    def canon_area(a, reg):
        return area_alias.get((reg, a), a)

    for reg in dict.fromkeys(l["region"] for l in logic):
        names = list(dict.fromkeys(a for l in logic if l["region"] == reg for a in l["areas"]))
        for a in names:
            longer = [x for x in names if x != a and x.startswith(a)]
            if longer:
                tgt = max(longer, key=len)
                area_alias[(reg, a)] = tgt
                issues.append(f"[提示] {reg}：列名 '{a}' 与 '{tgt}' 视为同一列（原刊前后页写法不一致），已合并")

    for l in logic:
        l["areas"] = list(dict.fromkeys(canon_area(a, l["region"]) for a in l["areas"]))
        for it in l["items"]:
            for p in it["prices"]:
                p["area"] = canon_area(p["area"], l["region"])

    # ---------- 4. 合并连续同类为章节
    sections = []
    for lt in logic:
        if sections and sections[-1]["region"] == lt["region"] and sections[-1]["kind"] == lt["kind"]:
            s = sections[-1]
            s["rows"].extend(lt["items"])
            s["areas"] = list(dict.fromkeys(s["areas"] + lt["areas"]))
            s["pages"][1] = max(s["pages"][1], lt["pages"][1])
            if lt["note"] and all(lt["note"][:30] != x[:30] for x in s["notes"]):
                s["notes"].append(lt["note"])
        else:
            sections.append({"region": lt["region"], "kind": lt["kind"], "pages": list(lt["pages"]),
                             "areas": list(lt["areas"]), "rows": list(lt["items"]),
                             "notes": [lt["note"]] if lt["note"] else []})

    for i, s in enumerate(sections):
        s["id"] = f"{i:02d}"
        s["label"] = TYPE_LABEL.get(s["kind"], s["kind"])
        s["title"] = f"{s['region']}{meta['period']}（第{meta['periodNo']}期）{s['label']}"
        s["itemCount"] = len(s["rows"])
        s["groups"] = list(dict.fromkeys(r["group"] for r in s["rows"] if r.get("group")))
        for r in s["rows"]:
            r.pop("_ri", None)

    payload = {"meta": meta, "issueKey": issue_key, "sections": sections}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{issue_key}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / f"{issue_key}.js").write_text(
        "window.__ISSUE_DATA__ = window.__ISSUE_DATA__ || {};\n"
        f"window.__ISSUE_DATA__[{json.dumps(issue_key, ensure_ascii=False)}] = "
        + json.dumps(payload, ensure_ascii=False) + ";\n", encoding="utf-8")

    idx_path = out_dir / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {"issues": []}
    idx["issues"] = [x for x in idx["issues"] if x["key"] != issue_key]
    idx["issues"].append({"key": issue_key, "label": f"{period}（总第{issue_no}期）", "period": period,
                          "issueNo": issue_no, "publishDate": pub, "file": f"{issue_key}.json",
                          "js": f"{issue_key}.js", "sections": len(sections),
                          "items": sum(s["itemCount"] for s in sections)})
    idx["issues"].sort(key=lambda x: (str(x["period"]), str(x["issueNo"])))
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "index.js").write_text("window.__ISSUE_INDEX__ = " + json.dumps(idx, ensure_ascii=False) + ";\n",
                                      encoding="utf-8")

    # ---------- 报告
    L = [f"文件        : {pdf_path.name}",
         f"期号        : {issue_key}    总页数 {meta['sourcePages']}",
         f"表格/逻辑表 : {len(raw)} / {len(logic)}    章节 {len(sections)}",
         f"价格条目    : {sum(s['itemCount'] for s in sections)}", ""]
    for s in sections:
        L.append(f"  p{s['pages'][0]:>3}-{s['pages'][1]:<3} {s['region']:<7}{s['label']:<14}"
                 f"条目{s['itemCount']:>4}  列: {', '.join(s['areas'])[:70]}")
    L += ["", f"提示 {len(issues)} 条"] + ["  " + x for x in issues]
    (out_dir / "_report.txt").write_text("\n".join(L), encoding="utf-8")
    doc.close()
    print("\n".join(L))
    print(f"\n无价格条目: {sum(1 for s in sections for r in s['rows'] if not r['prices'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
