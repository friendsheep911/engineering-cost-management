# -*- coding: utf-8 -*-
"""园林绿化苗木行业指导价抽取（青海省园林绿化协会发布，kind='seedling'）。

苗木表是独立附件（不在各地区材料价内），此前各期均未入库。表式简单：
序号 | 品种 | 规格 | 单位 | 价格(元) | 备注，单价格层、无地区维度。

用 PyMuPDF find_tables 按线框解析（文本流有名称/规格/单位黏连，不可靠）。
自动发现：先按「苗木」快筛候选页，再对候选页 find_tables 验证表头
（6 列且含「品种」「价格」），并从首个命中页向后连续扩展。

写入：data/<期次>.json 追加 section {kind:'seedling', region:'园林绿化协会', ...}
      prices=[{area:'园林绿化协会', incl:{raw,num}}]，并重建对应 .js 与 index 计数。

用法：
    python tools/extract_seedlings.py            # 扫描全部原始 PDF，补齐缺的期
    python tools/extract_seedlings.py --dry      # 只报告发现，不写盘
    python tools/extract_seedlings.py <pdf路径>   # 只处理一个 PDF
"""
import glob
import io
import json
import os
import re
import sys

import pymupdf

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SRC_DIRS = [
    os.path.join(ROOT, "..", "00_原始资料", "信息价"),
    "F:/SynologyDrive/工程文件/workbuddy工作区/水利数字化底座/00_原始资料/信息价",
]

AREA = "园林绿化协会"
KIND = "seedling"
LABEL = "常用园林绿化苗木行业指导价"


def is_seedling_table(tb):
    """表格列头像不像苗木表。"""
    if tb.col_count < 5 or tb.row_count < 2:
        return False
    head = [str(c or "").strip() for c in tb.extract()[0]]
    joined = "".join(head).replace(" ", "")
    return "品种" in joined and ("价格" in joined or "单价" in joined)


def parse_table(tb):
    """提取数据行。列序：序号|品种|规格|单位|价格(,备注)；5 列=无备注。"""
    ncol = tb.col_count
    rows = []
    for rec in tb.extract():
        cells = [str(c or "").strip().replace("\n", "") for c in rec]
        cells += [""] * (ncol - len(cells))
        if not any(cells):
            continue
        joined = "".join(cells)
        if joined.startswith("序号") or "品种" in cells[1]:
            continue
        if ncol >= 5:
            name, spec, unit, price = cells[1], cells[2], cells[3], cells[4]
            note = cells[5] if ncol >= 6 else ""
        elif ncol == 4:
            name, spec, unit, price, note = cells[0], cells[1], cells[2], cells[3], ""
        else:
            continue
        if not name:
            continue
        raw = (price or "").strip()
        pm = re.fullmatch(r"(\d[\d,]*(?:\.\d+)?)", raw.replace(",", ""))
        is_range = bool(re.fullmatch(r"\d[\d,]*(?:\.\d+)?\s*[-—~]\s*\d[\d,]*(?:\.\d+)?", raw))
        num = float(pm.group(1).replace(",", "")) if pm else None
        if not raw and num is None:
            continue
        rows.append({
            "name": name, "spec": spec or "", "unit": unit or "",
            "prices": [{"area": AREA, "incl": {"raw": raw, "num": num}, "excl": None}],
            "group": "", "note": ("区间价" if is_range else "") + (note or ""),
        })
    return rows


def extract_notes(page):
    """尾页「说明：/注：」后的文字，按 1、2、3… 分条。"""
    t = page.get_text()
    m = re.search(r"(?:说\s*明|备注)\s*[:：]", t)
    if not m:
        return []
    body = t[m.end():]
    lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    txt = "".join(lines)
    parts = re.split(r"(?=\d\s*[、.])", txt)
    parts = [p.strip() for p in parts if p.strip()]
    return parts or ([txt] if txt else [])


def text_table_parse(page):
    """无框表 fallback：按 x 坐标列锚解析（2022 上半年版式无线框）。
    列界：序号<80 | 品名<150 | 规格<430 | 单位<455 | 价格<505 | 备注>=505。
    必须存在同一表头行（含「品名/品种」且含「价格」），防止误吞其他附件表。"""
    ws = page.get_text("words")
    if not ws:
        return []
    # 表头行 y：「品名/品种」与「价格」两词 y 相距 <25pt 视为同一表头行（表头可能竖排跨两行）
    head_y = None
    name_ys = [w[1] for w in ws if w[4].strip() in ("品名", "品种") or "品种" in w[4]]
    price_ys = [w[1] for w in ws if "价格" in w[4]]
    for y1 in name_ys:
        for y2 in price_ys:
            if abs(y1 - y2) < 25:
                head_y = max(y1, y2) + 2
    if head_y is None:
        return []
    # 「说明：」尾注之后的词全部丢弃（防止黏进最后一行）
    note_ys = [w[1] for w in ws if w[4].strip().startswith("说明") or w[4].strip().startswith("注：")]
    if note_ys:
        ny = min(note_ys)
        if ny > head_y:
            ws = [w for w in ws if w[1] < ny]
    ws = [w for w in ws if w[1] > head_y]
    ws.sort(key=lambda w: (round(w[1] / 6), w[0]))
    rows, cur = [], None
    for x0, y0, x1, y1, t, *_ in ws:
        t = t.strip()
        if not t:
            continue
        if x0 < 80 and re.fullmatch(r"\d{1,3}", t):
            cur = {"name": [], "spec": [], "unit": [], "price": [], "note": [], "last_y": y0}
            rows.append(cur)
            continue
        if cur is None:
            continue
        if y0 - cur["last_y"] > 30:      # 与当前行相距过远：页脚/页眉，忽略
            continue
        cur["last_y"] = max(cur["last_y"], y0)
        if x0 < 150:
            cur["name"].append(t)
        elif x0 < 430:
            cur["spec"].append(t)
        elif x0 < 455:
            cur["unit"].append(t)
        elif x0 < 505:
            cur["price"].append(t)
        else:
            cur["note"].append(t)
    out = []
    for r in rows:
        name = "".join(r["name"]).strip()
        spec = "".join(r["spec"]).strip()
        unit = "".join(r["unit"]).strip()
        raw = "".join(r["price"]).strip()
        note = "".join(r["note"]).strip()
        if not name or not raw:
            continue
        pm = re.fullmatch(r"(\d[\d,]*(?:\.\d+)?)", raw.replace(",", ""))
        is_range = bool(re.fullmatch(r"\d[\d,]*(?:\.\d+)?\s*[-—~]\s*\d[\d,]*(?:\.\d+)?", raw))
        num = float(pm.group(1).replace(",", "")) if pm else None
        if not raw and num is None:
            continue
        out.append({
            "name": name, "spec": spec, "unit": unit,
            "prices": [{"area": AREA, "incl": {"raw": raw, "num": num}, "excl": None}],
            "group": "", "note": ("区间价" if is_range else "") + note,
        })
    return out


def page_content(i, doc):
    """该页苗木表数据行：优先 find_tables 线框解析，否则 x 坐标 fallback。"""
    tbs = []
    try:
        tbs = [tb for tb in doc[i].find_tables().tables if is_seedling_table(tb)]
    except Exception:
        pass
    if tbs:
        rows = []
        for tb in tbs:
            rows += parse_table(tb)
        return rows
    t = doc[i].get_text()
    if ("品名" in t or "品种" in t) and "价格" in t:
        return text_table_parse(doc[i])
    return []


def find_seedling_pages(doc):
    """定位苗木表页块。种子页 = 文本含「苗木」且有苗木线框表；
    之后向后逐页扩展（纯数据页不含「苗木」字样，不能只靠文本快筛），
    允许夹杂 1 页纯说明页；连续 2 页无表无说明即停。"""
    n = doc.page_count
    cand = set()
    for i in range(n):
        if "苗木" in doc[i].get_text():
            cand |= {i - 1, i, i + 1}    # 表页本身可能不含「苗木」字样
    seeds = [i for i in sorted(cand) if 0 <= i < n and page_content(i, doc)]
    if not seeds:
        return [], []
    start = min(seeds)
    pages, notes = [start], []
    i = start + 1
    gap = 0
    while i < n:
        rws = page_content(i, doc)
        if rws:
            pages.append(i)
            gap = 0
        else:
            nt = extract_notes(doc[i])
            if nt and "苗木" in doc[i].get_text():
                notes += nt
            gap += 1
            if gap >= 2:
                break
        i += 1
    # 尾页兜底收说明
    if not notes:
        for j in (pages[-1], pages[-1] + 1):
            if 0 <= j < n:
                notes += extract_notes(doc[j])
    return [(i, page_content(i, doc)) for i in pages], notes


def build_section(doc, blocks, notes):
    rows = []
    pages = []
    for idx, rws in blocks:
        pages.append(idx + 1)
        rows += rws
    # 去重：跨页重复行（同品种+规格+价格）
    seen, out = set(), []
    for r in rows:
        k = (r["name"], r["spec"], r["unit"],
             r["prices"][0]["incl"]["raw"] if r["prices"][0]["incl"] else "")
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    # 标题：从首个表页向前最多 3 页找《…苗木…价》书名号
    title = LABEL
    for j in range(pages[0] - 1, max(-1, pages[0] - 4), -1):
        mt = re.search(r"《([^》]{6,50}苗木[^》]{0,20}价[^》]*)》", doc[j].get_text())
        if mt:
            title = mt.group(1).replace(" ", "").replace("\n", "")
            break
    sec = {
        "region": "园林绿化协会", "kind": KIND, "pages": pages,
        "areas": [], "rows": out,
        "notes": notes, "id": "sl", "label": LABEL,
        "title": title,
        "itemCount": len(out), "groups": [],
    }
    return sec


def issue_map():
    """pdf文件名 -> (issueKey, json路径)。"""
    mp = {}
    for fn in glob.glob(os.path.join(DATA, "*.json")):
        base = os.path.basename(fn)
        if base.startswith(("index", "official", "_manifest")):
            continue
        try:
            d = json.load(open(fn, encoding="utf-8"))
        except Exception:
            continue
        sf = (d.get("meta") or {}).get("sourceFile")
        k = d.get("issueKey")
        if sf and k:
            mp[sf] = (k, fn)
    return mp


def patch_index(key, n_sec, n_items):
    for name, var in (("index.json", None), ("index.js", "window.__ISSUE_INDEX__ = ")):
        p = os.path.join(DATA, name)
        if not os.path.exists(p):
            continue
        txt = open(p, encoding="utf-8").read()
        if var:
            m = re.match(r"^" + re.escape(var) + r"(.*);\s*$", txt, re.S)
            d = json.loads(m.group(1))
        else:
            d = json.loads(txt)
        for it in d.get("issues", []):
            if it["key"] == key:
                it["sections"] = n_sec
                it["items"] = it.get("items", 0) + n_items if "items" in it else n_items
        if var:
            open(p, "w", encoding="utf-8").write(var + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + ";")
        else:
            open(p, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=1))


def process_pdf(pdf, dry=False, srcdir=None):
    mp = issue_map()
    name = os.path.basename(pdf)
    hit = mp.get(name)
    if not hit:
        return None, f"[跳过] {name}：data/ 中没有对应期次 JSON"
    key, jpath = hit
    doc = pymupdf.open(pdf)
    blocks, notes = find_seedling_pages(doc)
    if not blocks:
        return None, f"[无] {key}：{name} 未发现苗木表"
    sec = build_section(doc, blocks, notes)
    doc.close()
    if dry:
        return None, f"[发现] {key}：{name} p{sec['pages'][0]}-{sec['pages'][-1]} {sec['itemCount']} 行"
    d = json.load(open(jpath, encoding="utf-8"))
    old = [s for s in d["sections"] if s.get("kind") == KIND]
    if old:
        d["sections"] = [s for s in d["sections"] if s.get("kind") != KIND]
    # 按页码位置插入
    pos = len(d["sections"])
    for i, s in enumerate(d["sections"]):
        ps = s.get("pages") or [0]
        if ps and ps[0] > sec["pages"][0]:
            pos = i
            break
    d["sections"].insert(pos, sec)
    json.dump(d, open(jpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 重建 .js
    jsp = jpath[:-5] + ".js"
    payload = json.dumps({key: d}, ensure_ascii=False, separators=(",", ":"))
    open(jsp, "w", encoding="utf-8").write(
        "window.__ISSUE_DATA__ = window.__ISSUE_DATA__ || {};\n"
        f"window.__ISSUE_DATA__[{json.dumps(key, ensure_ascii=False)}] = "
        + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + ";")
    patch_index(key, len(d["sections"]), sec["itemCount"] - (sum(s["itemCount"] for s in old) if old else 0))
    return key, (f"[写入] {key}：{name} p{sec['pages'][0]}-{sec['pages'][-1]} "
                 f"{sec['itemCount']} 行（替换旧 {len(old)} 段）" if old else
                 f"[写入] {key}：{name} p{sec['pages'][0]}-{sec['pages'][-1]} {sec['itemCount']} 行")


def main():
    dry = "--dry" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    src = None
    for s in SRC_DIRS:
        if os.path.isdir(s):
            src = s
            break
    pdfs = args or sorted(glob.glob(os.path.join(src, "*.pdf")))
    if not pdfs:
        print("[错误] 找不到原始 PDF 目录", SRC_DIRS)
        return 2
    n = 0
    for pdf in pdfs:
        key, msg = process_pdf(pdf, dry=dry)
        if msg:
            print(msg)
        if key:
            n += 1
    print(f"完成：{n} 期写入/更新（dry={dry}）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
