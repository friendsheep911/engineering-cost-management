# -*- coding: utf-8 -*-
"""
转曲期 OCR 抽取
================
针对整本"文字转曲"（矢量字形、无文字层）的期次：
    渲染成位图 → RapidOCR 本地识别 → 行聚类 + 表头定位列 →
    输出与普通抽取完全一致的 data/<期号>.json + .js（meta.extractMethod="ocr"）。

用法：
    python tools/ocr_extract.py <PDF> --out data
    python tools/ocr_extract.py <PDF> --out data --pages 1-50     # 调试用
说明：
- 每页 OCR 结果缓存到 <out>/_ocr_cache/<PDF名>/p<N>.json，中断后重跑不重复识别
- 期号（总第N期）优先从封面 OCR 解析，其次按 index.json 相邻期次推断
- 依赖：pip install rapidocr-onnxruntime（隔离环境 envs/default 已装）
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_VENDOR = HERE.parent / "vendor"
if _VENDOR.exists() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from extract_info_price import parse_price, norm_unit          # noqa: E402
import pymupdf                                                  # noqa: E402

REGION_RE = re.compile(r"(西宁|海东|海西|黄南|海北|果洛|海南|玉树)\s*地区")
NAME_KW = ("材料名称", "机械名称", "工种名称", "项目名称")
SPEC_KW = ("型号规格", "强度等级", "规格")
# 表题行标记：含「指导价/参考价」的行是表标题（如「材料指导价（除税价格）」），
# 不是表头。若被吸进表头块，其「除税」字样会成为假 ptype 框，把所有价格列坍缩成一列。
TITLE_RE = re.compile(r"指导价|参考价|价格信息|市场信息")
ATTACH_RE = re.compile(r"价格指数|造价指标|指标分析|工程概况|消耗量指标|主要结构做法|附件\s*\d")
COUNTY_SUFFIX = ("县", "市", "区", "镇", "行委")
NUM_RE = re.compile(r"^-?[\d,.]+%?$")
PAGE_NO_RE = re.compile(r"^[·•\-\s]*\d{1,3}[\s·•\-]*$")

TYPE_LABEL = {"material": "材料指导价", "mortar": "干混砂浆", "green": "绿色建材",
              "labor": "劳务价格", "machine": "机械台班"}

# 分组行词表（多县表里的粗体类别行，右半页没有对应行，行配对时须跳过）
GROUP_WORDS = {
    "黑色金属", "有色金属", "木材", "水泥", "砂石", "管材", "管件", "阀门",
    "玻璃", "门窗", "电线、电缆", "电线电缆", "散热器", "散热器及配件",
    "装饰材料", "卫生洁具", "混凝土及制品", "敷线管槽配件", "建筑五金",
    "油漆、沥青、防水材料", "油漆、涂料", "沥青、防水材料", "防水材料",
    "保温、隔热、耐火材料", "保温、隔热、耐火", "保温材料", "耐火材料",
    "黑色、有色金属和五金制品", "灰、砂石、砖、水泥、混凝土及制品",
    "砖、砌块、灰、砂石", "水泥、混凝土", "砌筑材料", "金属制品",
    "其它", "其他", "种子", "插杆类",
}
_GROUP_SUFFIX = ("金属", "材料", "制品", "管材", "管件", "洁具", "五金", "涂料",
                 "水泥", "木材", "砖", "玻璃", "门窗", "阀门", "电缆", "其它",
                 "其他", "种子", "混凝土", "沥青", "配件", "器具", "砌块", "砂石",
                 "板材", "型材", "灯具", "电器", "化工", "油漆", "防水", "保温",
                 "耐火", "塑料", "橡胶")


def is_group_name(name):
    """识别分组行文本（如 有色金属/木材/水泥），区别于无价格的真实材料行。"""
    t = (name or "").strip()
    if not t or re.search(r"\d", t):
        return False
    if t in GROUP_WORDS:
        return True
    n = re.sub(r"[、，,\s·（）()]", "", t)
    return len(n) <= 6 and n.endswith(_GROUP_SUFFIX)


def kind_of(text):
    if "机械台班" in text:
        return "machine"
    if "劳务" in text or "人工成本" in text:
        return "labor"
    if "干混砂浆" in text:
        return "mortar"
    if "绿色建筑" in text:
        return "green"
    return "material"


def kind_from_lines(lines):
    """从表头之前的标题行推断表类型（跳过 ·指导价格· 之类的页眉）。"""
    for l in lines:
        t = l["text"]
        if len(t.strip()) < 12 or re.fullmatch(r"[·•\-\s]*[^·•\s]{2,8}[·•\-\s]*", t):
            continue
        k = kind_of(t)
        if k != "material":
            return k
        if "材料" in t or "参考价" in t:
            return "material"
    return "material"


def is_county_text(t):
    t = (t or "").strip()
    if not t or len(t) > 12 or re.search(r"\d", t):
        return False
    base = re.sub(r"[（(][^）)]*[）)]?", "", t)      # 去掉（含西海镇）等括注
    if not base:
        return False
    if base.endswith(COUNTY_SUFFIX):
        return True
    return base in ("尖扎", "同仁", "泽库", "河南", "德令哈", "乌兰", "都兰", "天峻",
                    "格尔木", "柴旦", "冷湖", "茫崖", "玛沁", "班玛", "甘德", "达日",
                    "久治", "玛多", "称多", "囊谦", "杂多", "治多", "曲麻莱", "共和",
                    "贵德", "贵南", "同德", "兴海", "民和", "互助", "化隆", "循化",
                    "乐都", "平安", "西宁", "海晏", "祁连", "门源", "刚察", "玉树")


def ocr_page(engine, page, dpi):
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    result, _ = engine(img)
    boxes = []
    if result:
        for box, text, score in result:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            # 坐标还原到 PDF 点
            k = 72.0 / dpi
            boxes.append({"x0": min(xs) * k, "y0": min(ys) * k,
                          "x1": max(xs) * k, "y1": max(ys) * k,
                          "text": str(text), "score": float(score)})
    return boxes


HEAD_TOKS = ("含税价格", "除税价格", "含税", "除税", "（元", "(元", "单位", "备注",
             "材料名称", "型号规格", "序号", "计量")


def _is_head_tok(t):
    """完整表头词短框（列头），相邻时禁止拼接，否则多列会被粘成一列。"""
    t = (t or "").strip()
    if not t or len(t) > 8:
        return False
    return any(t.startswith(w) or t.endswith(w) for w in HEAD_TOKS)


def group_lines(boxes, y_tol=3.5):
    """按 y 中心聚类成行，行内按 x 排序；同行相邻盒子拼接。"""
    if not boxes:
        return []
    hs = sorted(b["y1"] - b["y0"] for b in boxes)
    med_h = hs[len(hs) // 2]
    tol = max(y_tol, med_h * 0.45)
    boxes = sorted(boxes, key=lambda b: ((b["y0"] + b["y1"]) / 2, b["x0"]))
    lines, cur, cur_y = [], [], None
    for b in boxes:
        y = (b["y0"] + b["y1"]) / 2
        if cur_y is None or abs(y - cur_y) <= tol:
            cur.append(b)
            cur_y = sum((x["y0"] + x["y1"]) / 2 for x in cur) / len(cur)
        else:
            lines.append(cur)
            cur, cur_y = [b], y
    if cur:
        lines.append(cur)
    out = []
    for ln in lines:
        ln.sort(key=lambda b: b["x0"])
        merged = []
        for b in ln:
            if merged and b["x0"] - merged[-1]["x1"] < 7 \
                    and not (_is_head_tok(merged[-1]["text"]) and _is_head_tok(b["text"])):
                m = merged[-1]                              # 同一逻辑框被拆开
                m["x1"] = max(m["x1"], b["x1"])
                m["text"] += b["text"]
            else:
                merged.append(dict(b))
        out.append({"y": sum((b["y0"] + b["y1"]) / 2 for b in merged) / len(merged),
                    "boxes": merged,
                    "text": "".join(b["text"] for b in merged)})
    out.sort(key=lambda l: l["y"])
    return out


def find_header(lines):
    for i, l in enumerate(lines):
        t = l["text"]
        if any(k in t for k in NAME_KW) and not ATTACH_RE.search(t):
            return i
    return None


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


class Table:
    """一页中的一张表：列定义 + 数据行。"""

    def __init__(self, region, kind, header_y, cols, body_lines, notes):
        self.region, self.kind = region, kind
        self.header_y, self.cols = header_y, cols
        self.body, self.notes = body_lines, notes

    def items(self, default_area, raw=False):
        cols = self.cols
        # 预合并：同一印刷行的 OCR 断行（y 差 < 5pt）拼回一行
        rows = []
        for ln in self.body:
            if rows and abs(ln["y"] - rows[-1]["y"]) < 5.0:
                rows[-1]["boxes"] += ln["boxes"]
                rows[-1]["text"] += ln["text"]
            else:
                rows.append({"y": ln["y"], "boxes": list(ln["boxes"]),
                             "text": ln["text"]})
        # 碎片合并：名称列竖排换行碎片（无价格内容、非分组、短文本）并入最近价格行
        price_cols = [c for c in cols if c["role"] == "price"]

        def has_price_content(r):
            for b in r["boxes"]:
                for c in price_cols:
                    if overlap(b["x0"], b["x1"], c["x0"], c["x1"]) > \
                            0.3 * max(1.0, b["x1"] - b["x0"]):
                        return True
            return False

        priced = [r for r in rows if has_price_content(r)]
        frag = set()
        for i, r in enumerate(rows):
            t = r["text"]
            if has_price_content(r) or not r["boxes"] or is_group_name(t) \
                    or len(t) > 6 or re.search(r"\d", t):
                continue
            cand = min(priced, key=lambda p: abs(p["y"] - r["y"]), default=None)
            if cand is not None and abs(cand["y"] - r["y"]) < 13:
                cand["boxes"] += r["boxes"]
                cand["text"] += t
                frag.add(i)
        rows = [r for i, r in enumerate(rows) if i not in frag]
        items, cur_name, cur_spec = [], "", ""
        for ln in rows:
            cell = {}
            for c in cols:
                cell.setdefault((c["role"], c["cid"]), [])
            for b in ln["boxes"]:
                cx = (b["x0"] + b["x1"]) / 2
                best, bestv = None, 0.0
                for c in cols:
                    v = overlap(b["x0"], b["x1"], c["x0"], c["x1"])
                    if v > bestv:
                        best, bestv = c, v
                if bestv <= 0:                     # 兜底：最近列中心
                    cand = min(cols, key=lambda c: abs((c["x0"] + c["x1"]) / 2 - cx))
                    if abs((cand["x0"] + cand["x1"]) / 2 - cx) > 25:
                        continue                   # 离所有列都远：多半是漏识别的县份列，丢弃
                    best = cand
                if best["role"] == "price" and \
                        len(re.findall(r"[\u4e00-\u9fff]", b["text"])) >= 2:
                    continue                       # 价格列里的中文块（备注/脚注），非价格
                cell.setdefault((best["role"], best["cid"]), []).append((best, b["text"]))

            def g(role):
                """按 role 合并该 role 下所有列的内容（名称/规格/单位/备注各仅一列）。"""
                acc = []
                for c in cols:
                    if c["role"] == role:
                        acc += cell.get((role, c["cid"]), [])
                return "".join(t for _, t in sorted(acc, key=lambda z: z[0]["x0"]))

            def g_col(c):
                return "".join(t for _, t in sorted(cell.get(("price", c["cid"]), []),
                                                    key=lambda z: z[0]["x0"]))

            name, spec, unit = g("name"), g("spec"), g("unit")
            if name and not name.isdigit():
                cur_name, cur_spec = name, spec
            elif spec:                             # 续行只带规格（如 "Φ 10mm 以外"）
                cur_spec = spec
            prices = []
            for c in cols:
                if c["role"] != "price":
                    continue
                p = parse_price(g_col(c))
                if not p or p.get("num") is None:
                    continue               # 无数字内容（(元)/表头残渣）不作价格
                area = c["area"] or default_area
                slot = next((q for q in prices if q["area"] == area), None)
                if slot is None:
                    slot = {"area": area, "incl": None, "excl": None}
                    prices.append(slot)
                if c["ptype"] == "excl":
                    slot["excl"] = p
                else:
                    slot["incl"] = p
            if not cur_name and not prices:
                continue
            is_group = (bool(cur_name) and not prices
                        and not re.search(r"\d", cur_name) and is_group_name(cur_name))
            items.append({"name": cur_name, "spec": cur_spec,
                          "unit": norm_unit(unit), "prices": prices,
                          "group": "", "note": g("note"), "_group_row": is_group,
                          "_own_name": bool(name), "_y": ln["y"]})
        if raw:                      # 原始行：不消费分组行，供跨页行配对使用
            for it in items:
                it.pop("_own_name", None)
            return items
        # 分组行：把名字写进后续行的 group；本身不作为数据行
        out, group = [], ""
        for it in items:
            if it.pop("_group_row"):
                group = it["name"]
                continue
            it["group"] = group
            if not it["name"]:
                continue
            out.append(it)
        return out


def _header_block(lines, hi):
    """表头块收集：anchor + 向上紧邻的「含税/除税」价格行或县名行 +
    向下紧邻的「（元）/县名」等表头行。返回 (block, body起始行号)。"""
    anchor = lines[hi]
    block = [anchor]
    k = hi - 1
    while k >= 0:
        t = lines[k]["text"]
        n_county = sum(1 for b in lines[k]["boxes"] if is_county_text(b["text"]))
        if (not TITLE_RE.search(t)) \
                and ((("含税" in t or "除税" in t) and not any(x in t for x in NAME_KW)) \
                     or n_county >= 2):   # 含税/除税行，或县名行（在「材料名称」行上方）
            block.insert(0, lines[k])
            k -= 1
        else:
            break
    j = hi + 1
    while j < len(lines):
        t = lines[j]["text"]
        if PAGE_NO_RE.match(t) or not t.strip():
            j += 1
            continue
        has_price_data = any(NUM_RE.match(b["text"].replace(",", ""))
                             for b in lines[j]["boxes"] if len(b["text"]) <= 12)
        if has_price_data or ATTACH_RE.search(t):
            break
        headlike = (re.match(r"^[（(]?\s*元\s*[)）]?$", t.strip())
                    or is_county_text(t) or "含税" in t or "除税" in t
                    or "单位" in t or "计量" in t or "序号" in t
                    or sum(1 for b in lines[j]["boxes"] if is_county_text(b["text"])) >= 2)
        if not headlike:                    # 遇到分组行(如"黑色金属")即停，留给正文
            break
        block.append(lines[j])
        j += 1
        if len(block) >= 6:
            break
    return block, j


def build_table(lines, hi, region, kind, pno=None):
    """从表头行 hi 开始构建 Table；失败返回 None。"""
    block, j = _header_block(lines, hi)
    # 过滤数据区里的页码/脚注
    notes, data_lines = [], []
    for l in lines[j:]:
        t = l["text"]
        if PAGE_NO_RE.match(t):
            continue
        if re.match(r"^\s*注\s*[：:1-9１-９]", t):
            notes.append(t)
            continue
        data_lines.append(l)
    if not data_lines:
        return None

    # ---- 列识别
    ptype_boxes = [b for l in block for b in l["boxes"]
                   if "含税" in b["text"] or "除税" in b["text"]]
    county_boxes = [b for l in block for b in l["boxes"] if is_county_text(b["text"])]
    if pno is not None:
        county_boxes = graft_counties(county_boxes, region, kind, pno)
    anchor_boxes = [b for l in block for b in l["boxes"]]
    cols = []

    def add_col(x0, x1, role, area=None, ptype=None):
        for c in cols:
            if overlap(x0, x1, c["x0"], c["x1"]) > 0.5 * min(x1 - x0, c["x1"] - c["x0"]):
                if role == "price" and c["role"] == "price" and ptype:
                    c["ptype"] = ptype                      # 同列先含税后除税？不会发生
                return
        cols.append({"x0": x0, "x1": x1, "role": role,
                     "area": area, "ptype": ptype, "cid": str(len(cols))})

    if ptype_boxes:
        px0 = min(b["x0"] for b in ptype_boxes)
        px1 = max(b["x1"] for b in ptype_boxes)
        # 价格列：每个含税/除税框一列，县名按 x 归属
        def county_at(x):
            if not county_boxes:
                return None                 # 单地区表（无县名行）：交由 default_area
            cb = min(county_boxes, key=lambda b: abs((b["x0"] + b["x1"]) / 2 - x))
            if abs((cb["x0"] + cb["x1"]) / 2 - x) > 45:
                return _COUNTY_DROP         # 孤立价格列（县名 OCR 漏检）：标记丢弃
            return cb["text"]
        for b in sorted(ptype_boxes, key=lambda b: b["x0"]):
            cx = (b["x0"] + b["x1"]) / 2
            a = county_at(cx)
            if a == _COUNTY_DROP:
                continue                    # 县名无法归属的列：丢弃，防价格错挂到别的县
            add_col(b["x0"], b["x1"], "price",
                    area=a, ptype="excl" if "除税" in b["text"] else "incl")
        # 标签列：价格区之外的 anchor 框
        for b in anchor_boxes:
            t = b["text"]
            if overlap(b["x0"], b["x1"], px0, px1) > 0.4 * (b["x1"] - b["x0"]):
                continue
            if any(k in t for k in NAME_KW):
                add_col(b["x0"], b["x1"], "name")
            elif any(k in t for k in SPEC_KW):
                add_col(b["x0"], b["x1"], "spec")
            elif "单位" in t:
                add_col(b["x0"], b["x1"], "unit")
            elif "备注" in t:
                add_col(b["x0"], b["x1"], "note")
    else:
        # 单价格层（劳务/机械/砂浆等）：非名称/规格/单位/备注的表头框 = 价格列
        for b in anchor_boxes:
            t = b["text"]
            if any(k in t for k in NAME_KW):
                add_col(b["x0"], b["x1"], "name")
            elif any(k in t for k in SPEC_KW):
                add_col(b["x0"], b["x1"], "spec")
            elif "单位" in t:
                add_col(b["x0"], b["x1"], "unit")
            elif "备注" in t:
                add_col(b["x0"], b["x1"], "note")
        price_zone = [b for b in anchor_boxes
                      if not any(overlap(b["x0"], b["x1"], c["x0"], c["x1"]) /
                                 max(1e-6, min(b["x1"] - b["x0"], c["x1"] - c["x0"])) > 0.5
                                 for c in cols)]
        for b in price_zone:
            t = b["text"]
            if not t or "序号" in t:
                continue
            if is_county_text(t) and len(cols) == 0:
                continue
            if is_county_text(t):
                add_col(b["x0"], b["x1"], "price", area=t, ptype="incl")
            else:
                add_col(b["x0"], b["x1"], "price", ptype="incl")
        # 兄弟页借入的县名框（本页 OCR 漏检县名）：本页 anchor 无此框，需补建价格列
        for b in county_boxes:
            t = b["text"]
            if not is_county_text(t):
                continue
            if any(overlap(b["x0"], b["x1"], c["x0"], c["x1"]) /
                   max(1e-6, min(b["x1"] - b["x0"], c["x1"] - c["x0"])) > 0.5
                   for c in cols):
                continue                        # 本页已检测到，列已建
            add_col(b["x0"], b["x1"], "price", area=t, ptype="incl")
    cols.sort(key=lambda c: c["x0"])
    for i, c in enumerate(cols):
        c["cid"] = str(i)
    if not any(c["role"] == "name" for c in cols) or \
       not any(c["role"] == "price" for c in cols):
        return None
    if sum(1 for c in cols if c["role"] == "price") < 1:
        return None
    # 表头 y 之后还有别的表？正文若出现第二次表头则截断
    cut = len(lines)
    for k in range(j, len(lines)):
        if any(x in lines[k]["text"] for x in NAME_KW) and "单位" in lines[k]["text"]:
            cut = k
            break
    data_lines = [l for l in data_lines if l["y"] < (lines[cut]["y"] if cut < len(lines) else 10 ** 9)]
    return Table(region, kind, lines[hi]["y"], cols, data_lines, notes)


def find_price_header(lines):
    """右半页列头：本页无「材料名称」表头，但前几行出现 ≥2 个县名框的行。"""
    for i, l in enumerate(lines[:8]):
        if sum(1 for b in l["boxes"] if is_county_text(b["text"])) >= 2:
            return i
        if any(NUM_RE.match(b["text"].replace(",", "")) for b in l["boxes"]):
            break                       # 已经进入数据区，本页不是右半页
    return None


LABEL_ROLES = (("名称", "name"), ("型号", "spec"), ("规格", "spec"),
               ("单位", "unit"), ("备注", "note"))

_COUNTY_DROP = "\x00DROP"       # county_at 哨兵：列中心距最近县名过远，整列丢弃

# ---- 跨页县名框互补 --------------------------------------------------------
# 同一张表的相邻页版式一致（如海西 A 页=德令哈+乌兰、B 页=都兰..茫崖）。某页 OCR
# 漏检/误识县名框时，从「同 region+kind 且存在同名县且 x0 一致(±10pt)」的兄弟页
# 借入缺失的县名框，避免其价格列因无法归属而被丢弃。
_COUNTY_MEM = []                # [(region, kind, pno, [box, ...])]


def remember_counties(boxes, region, kind, pno):
    if boxes:
        _COUNTY_MEM.append((region, kind, pno, [dict(b) for b in boxes]))


def graft_counties(own, region, kind, pno):
    if not own:
        return own
    merged = list(own)
    for rg, kd, sp, sib in _COUNTY_MEM:
        if rg != region or kd != kind or sp == pno:
            continue
        # 锚定：兄弟页与当前页至少有一个同名县且 x0 相近，才视为同一版式系列
        if not any(s["text"] == o["text"] and abs(s["x0"] - o["x0"]) <= 10
                   for s in sib for o in own):
            continue
        for s in sib:
            if s["text"] not in {o["text"] for o in merged} \
                    and all(abs(s["x0"] - o["x0"]) > 10 for o in merged):
                merged.append(dict(s))
    return merged


def build_county_table(lines, hi, region, kind, pno=None):
    """宽松县名列表：县名行 ± 含税/除税行 ± 标签行（名称/型号/单位）。

    有名称列 → 机械台班/劳务等独立新表；无名称列 → 左页表的右半页（仅价格列）。
    """
    county_boxes = [b for b in lines[hi]["boxes"] if is_county_text(b["text"])]
    if pno is not None:
        county_boxes = graft_counties(county_boxes, region, kind, pno)
    if len(county_boxes) < 2:
        return None

    def headlike_line(t):
        if TITLE_RE.search(t):              # 表题行（含「指导价」等）不是表头
            return False
        return ("含税" in t or "除税" in t
                or any(kw in t for kw, _ in LABEL_ROLES)
                or re.match(r"^[（(]\s*元\s*[)）]", t.strip())
                or "租赁价格" in t or "价格（元）" in t)

    lo_i = hi_i = hi
    k = hi - 1
    while k >= 0 and lo_i > hi - 4:
        if headlike_line(lines[k]["text"]):
            lo_i = k
            k -= 1
        else:
            break
    k = hi + 1
    while k < len(lines) and hi_i < hi + 4:
        if headlike_line(lines[k]["text"]):
            hi_i = k
            k += 1
        else:
            break
    block = lines[lo_i:hi_i + 1]
    body = []
    for l in lines[hi_i + 1:]:
        t = l["text"]
        if PAGE_NO_RE.match(t):
            continue
        if re.match(r"^\s*注\s*[：:1-9１-９]", t):
            continue
        body.append(l)
    if not body:
        return None
    block_boxes = [b for l in block for b in l["boxes"]]
    ptype_boxes = [b for b in block_boxes if "含税" in b["text"] or "除税" in b["text"]]
    cols = []

    def add_col(x0, x1, role, area=None, ptype=None):
        for c in cols:
            if overlap(x0, x1, c["x0"], c["x1"]) > 0.5 * min(x1 - x0, c["x1"] - c["x0"]):
                return
        cols.append({"x0": x0, "x1": x1, "role": role,
                     "area": area, "ptype": ptype, "cid": str(len(cols))})

    if ptype_boxes:
        def county_at(x):
            if not county_boxes:
                return None                 # 单地区表（无县名行）：交由 default_area
            cb = min(county_boxes, key=lambda b: abs((b["x0"] + b["x1"]) / 2 - x))
            if abs((cb["x0"] + cb["x1"]) / 2 - x) > 45:
                return _COUNTY_DROP         # 孤立价格列（县名 OCR 漏检）：标记丢弃
            return cb["text"]
        for b in sorted(ptype_boxes, key=lambda b: b["x0"]):
            cx = (b["x0"] + b["x1"]) / 2
            a = county_at(cx)
            if a == _COUNTY_DROP:
                continue                    # 县名无法归属的列：丢弃，防价格错挂到别的县
            add_col(b["x0"], b["x1"], "price",
                    area=a, ptype="excl" if "除税" in b["text"] else "incl")
    else:
        for cb in sorted(county_boxes, key=lambda b: b["x0"]):
            add_col(cb["x0"], cb["x1"], "price", area=cb["text"], ptype="incl")
    priced = [c for c in cols if c["role"] == "price"]
    pspan = ((min(c["x0"] for c in priced), max(c["x1"] for c in priced))
             if priced else (0, 0))
    for b in block_boxes:
        t = b["text"]
        if is_county_text(t) or "含税" in t or "除税" in t:
            continue
        if overlap(b["x0"], b["x1"], pspan[0], pspan[1]) > 0.4 * max(1.0, b["x1"] - b["x0"]):
            continue
        for kw, role in LABEL_ROLES:
            if kw in t:
                add_col(b["x0"], b["x1"], role)
                break
    cols.sort(key=lambda c: c["x0"])
    for i, c in enumerate(cols):
        c["cid"] = str(i)
    if not any(c["role"] == "price" for c in cols):
        return None
    return Table(region, kind, lines[hi]["y"], cols, body, [])


def merge_right_prices(entry, rows_r, pno):
    """把右半页价格按行 y 坐标配对合并进左页原始行（左右是同一张表的两半）。"""
    raw = [r for r in entry["raw"] if not r["_group_row"]]
    if not rows_r:
        return 0
    used, n, miss = set(), 0, 0
    for b in rows_r:
        # 优先配左页有价格的行，其次任意行
        cands = [r for r in raw if r["prices"] and id(r) not in used] or \
                [r for r in raw if id(r) not in used]
        a = min(cands, key=lambda r: abs(r["_y"] - b["_y"]), default=None)
        if a is None or abs(a["_y"] - b["_y"]) > 6.0:
            miss += 1
            continue
        used.add(id(a))
        for pr in b["prices"]:
            slot = next((q for q in a["prices"] if q["area"] == pr["area"]), None)
            if slot is None:
                a["prices"].append(pr)
                slot = pr
            if pr.get("incl") is not None:
                slot["incl"] = pr["incl"]
            if pr.get("excl") is not None:
                slot["excl"] = pr["excl"]
            n += 1
    if miss:
        print(f"  [警告] p{pno} 右半页 {miss}/{len(rows_r)} 行未找到左页对应行（y 配对失败）")
    return n


def finalize_rows(raw):
    """分组行消费 + 无名行过滤 → 最终条目。"""
    out, group = [], ""
    for it in raw:
        it = dict(it)
        it.pop("_y", None)
        if it.pop("_group_row", False):
            group = it["name"]
            continue
        it["group"] = group
        it.pop("_own_name", None)
        if not it["name"]:
            continue
        out.append(it)
    return out


def parse_issue_no(texts):
    for t in texts:
        m = re.search(r"总\s*第\s*(\d{1,4})\s*期", t)
        if m:
            return m.group(1)
    return None


def infer_issue_no(out_dir, pub):
    """按 index.json 的 (发布日期, 总期号) 推断缺失期号。"""
    idx_path = out_dir / "index.json"
    if not idx_path.exists() or not pub:
        return None
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        pts = sorted((x["publishDate"][:10], str(x["issueNo"]))
                     for x in idx["issues"] if x.get("issueNo") and str(x["issueNo"]).isdigit())
    except Exception:
        return None
    below = [p for p in pts if p[0] < pub[:10]]
    above = [p for p in pts if p[0] > pub[:10]]
    if not below or not above:
        return None
    lo, hi = int(below[-1][1]), int(above[0][1])
    if hi - lo == 2:
        return str(lo + 1)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", default="data")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--pages", default=None, help="如 1-60（调试用）")
    ap.add_argument("--cache-dir", default=None,
                    help="OCR 页缓存目录（默认 <out>/_ocr_cache）")
    ap.add_argument("--no-write", action="store_true",
                    help="只做 OCR 填充缓存，不写 JSON/索引（供分片并行调用）")
    ap.add_argument("--max-pages", type=int, default=0)
    args = ap.parse_args()

    # 限制 ONNX Runtime 线程：默认会用满物理核，多进程并行时严重超订（实测每页 18s → 1.5s）
    _nt = os.environ.get("OCR_INTRA_THREADS")
    if _nt:
        import onnxruntime as _ort
        _n = int(_nt)

        class _SO(_ort.SessionOptions):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.intra_op_num_threads = _n
                self.inter_op_num_threads = 1

        _ort.SessionOptions = _SO          # 必须在 import rapidocr 之前生效

    from rapidocr_onnxruntime import RapidOCR
    # 关闭方向分类（表内全为横排文字，可省约 30% 时间）；加大识别批大小
    _ekw = {"text_score": float(os.environ.get("OCR_TEXT_SCORE", "0.35"))}
    if os.environ.get("OCR_NO_CLS"):
        _ekw["use_angle_cls"] = False
    if os.environ.get("OCR_REC_BATCH"):
        # 注意：本库 update_rec_params 会重建 Rec 配置，必须同时给 model_path，否则 KeyError
        from rapidocr_onnxruntime.utils import root_dir as _rd
        _ekw["rec_batch_num"] = int(os.environ["OCR_REC_BATCH"])
        _ekw["rec_model_path"] = str(_rd / "models" / "ch_PP-OCRv3_rec_infer.onnx")
    engine = RapidOCR(**_ekw)

    pdf_path = Path(args.pdf)
    out_dir = Path(args.out)
    cache_dir = Path(args.cache_dir) if args.cache_dir else out_dir / "_ocr_cache"
    cache_dir = cache_dir / pdf_path.stem
    cache_dir.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(pdf_path)
    n_pages = doc.page_count
    if args.pages:
        a, b = args.pages.split("-")
        lo, hi = int(a), min(int(b), n_pages)
    else:
        lo, hi = 1, n_pages
    if args.max_pages:
        hi = min(hi, args.max_pages)

    # ---- 逐页 OCR（带缓存）
    all_lines, page_boxes = {}, {}
    for pno in range(lo, hi + 1):
        cf = cache_dir / f"p{pno}.json"
        if cf.exists():
            boxes = json.loads(cf.read_text(encoding="utf-8"))
        else:
            boxes = ocr_page(engine, doc[pno - 1], args.dpi)
            cf.write_text(json.dumps(boxes, ensure_ascii=False), encoding="utf-8")
        page_boxes[pno] = boxes
        all_lines[pno] = group_lines(boxes)
        if pno % 10 == 0 or pno == hi:
            print(f"  OCR 进度 p{pno}/{hi}", flush=True)

    # ---- 期号 / 发布日期：以官网期次清单为权威来源（统一为「YYYY年第N期」）
    front_texts = [l["text"] for p in range(1, min(6, n_pages) + 1)
                   for l in all_lines.get(p, [])]
    mf = re.search(r"(\d{4})\s*年\s*第\s*(\d+)\s*期", pdf_path.name)
    off_issue, off_no, off_pub = "", "", ""
    off_path = out_dir / "official_issues.json"
    if off_path.exists():
        try:
            off = json.loads(off_path.read_text(encoding="utf-8"))
            for i, o in enumerate(off.get("issues", [])):
                hit = o.get("attach", "").endswith(pdf_path.name)      # 按附件名精确匹配
                if not hit and mf:                                     # 按文件名期号匹配
                    hit = o.get("issue") == f"{mf.group(1)}年第{int(mf.group(2))}期"
                if hit:
                    off_issue = o.get("issue", "")
                    off_no = str(131 + i + 1)                          # 2020年第1期 = 总第132期
                    off_pub = (o.get("published") or "")[:10]
                    break
        except Exception:
            pass
    issue_no = off_no or parse_issue_no(front_texts)
    period = off_issue or (f"{mf.group(1)}年第{mf.group(2)}期" if mf else "")
    pub = off_pub
    if not pub:
        mpub = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", " ".join(front_texts))
        if mpub:
            pub = f"{mpub.group(1)}-{int(mpub.group(2)):02d}-01"
    if not issue_no:
        issue_no = infer_issue_no(out_dir, pub)
    if not issue_no:
        print("[错误] 期号（总第N期）解析失败，且无法从相邻期次推断")
        return 2
    if not period:
        period = issue_no
    _m2 = re.search(r"第\s*(\d+)\s*期", period)
    period_no = _m2.group(1) if _m2 else ""
    issue_key = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", period)
    print(f"期号: {period}（总第{issue_no}期）  发布: {pub}")

    # ---- 预扫描：登记各页表头的县名框（供跨页互补 OCR 漏检县名）
    _reg0 = ""
    for pno in range(lo, hi + 1):
        lines = all_lines.get(pno) or []
        if not lines:
            continue
        page_text = " ".join(l["text"] for l in lines)
        if ATTACH_RE.search(page_text) and not REGION_RE.search(page_text):
            continue
        rm = REGION_RE.search(page_text)
        if rm:
            _reg0 = rm.group(1) + "地区"
        h = find_header(lines)
        if h is not None:
            cbs = [b for l in _header_block(lines, h)[0] for b in l["boxes"]
                   if is_county_text(b["text"])]
            remember_counties(cbs, _reg0, kind_from_lines(lines[:h]), pno)
        else:
            r = find_price_header(lines)
            if r is not None:
                cbs = [b for b in lines[r]["boxes"] if is_county_text(b["text"])]
                remember_counties(cbs, _reg0, kind_from_lines(lines[:r]), pno)

    # ---- 逐页解析表格（含续页复用上一页列定义、右半页价格合并）
    logic, cur_region = [], ""
    last_cols = last_kind = last_region = None
    for pno in range(lo, hi + 1):
        lines = all_lines[pno]
        if not lines:
            continue
        page_text = " ".join(l["text"] for l in lines)
        if ATTACH_RE.search(page_text) and not REGION_RE.search(page_text):
            continue
        rm = REGION_RE.search(page_text)
        if rm:
            cur_region = rm.group(1) + "地区"
        hi_idx = find_header(lines)
        right_idx = find_price_header(lines) if hi_idx is None else None
        if hi_idx is not None:
            kind = kind_from_lines(lines[:hi_idx])
            tb = build_table(lines, hi_idx, cur_region, kind, pno=pno)
            if tb:
                last_cols, last_kind, last_region = tb.cols, tb.kind, cur_region
                raw = tb.items(cur_region, raw=True)
                if any(r["prices"] or r["name"] for r in raw):
                    logic.append({"region": tb.region, "kind": tb.kind,
                                  "pages": [pno, pno], "raw": raw,
                                  "notes": list(dict.fromkeys(tb.notes))})
        elif right_idx is not None:
            tb_r = build_county_table(lines, right_idx, cur_region,
                                      last_kind or "material", pno=pno)
            if tb_r and any(c["role"] == "name" for c in tb_r.cols):
                # 宽松表头新表（机械台班/劳务等）：作为独立条目
                kind = kind_from_lines(lines[:right_idx])
                if "机械" in page_text:
                    kind = "machine"
                elif "劳务" in page_text or "工种" in page_text:
                    kind = "labor"
                tb_r.kind = kind
                raw = tb_r.items(cur_region, raw=True)
                if any(r["prices"] or r["name"] for r in raw):
                    last_cols, last_kind, last_region = tb_r.cols, kind, cur_region
                    logic.append({"region": cur_region, "kind": kind,
                                  "pages": [pno, pno], "raw": raw, "notes": []})
            elif tb_r and logic and logic[-1]["region"] == cur_region:
                # 右半页：与上一页（左半页）按行 y 坐标配对，补齐右侧县份价格
                rows_r = [r for r in tb_r.items(cur_region, raw=True) if r["prices"]]
                if merge_right_prices(logic[-1], rows_r, pno):
                    logic[-1]["pages"][1] = pno
        elif last_cols is not None and cur_region == last_region:
            # 续表：本页无表头，复用上一页的列定义
            data_lines = [l for l in lines
                          if l["text"].strip() and not PAGE_NO_RE.match(l["text"])
                          and not ATTACH_RE.search(l["text"])]
            tb = (Table(cur_region, last_kind, 0.0, last_cols, data_lines, [])
                  if data_lines else None)
            if tb is not None:
                raw = tb.items(cur_region, raw=True)
                if any(r["prices"] or r["name"] for r in raw):
                    logic.append({"region": tb.region, "kind": tb.kind,
                                  "pages": [pno, pno], "raw": raw, "notes": []})

    # ---- 原始行定稿为条目，合并连续同类为章节
    sections = []
    for lt in logic:
        items = [it for it in finalize_rows(lt["raw"]) if it["prices"]]
        if not items:
            continue
        areas = list(dict.fromkeys(
            p["area"] for it in items for p in it["prices"] if p["area"]))
        lt = {"region": lt["region"], "kind": lt["kind"], "pages": lt["pages"],
              "areas": areas, "items": items, "notes": lt["notes"]}
        if sections and sections[-1]["region"] == lt["region"] and sections[-1]["kind"] == lt["kind"]:
            s = sections[-1]
            s["rows"].extend(lt["items"])
            s["areas"] = list(dict.fromkeys(s["areas"] + lt["areas"]))
            s["pages"][1] = max(s["pages"][1], lt["pages"][1])
            for n in lt["notes"]:
                if n not in s["notes"]:
                    s["notes"].append(n)
        else:
            sections.append({"region": lt["region"], "kind": lt["kind"],
                             "pages": list(lt["pages"]), "areas": list(lt["areas"]),
                             "rows": list(lt["items"]), "notes": list(lt["notes"])})
    meta = {"title": "青海工程造价管理信息", "issue": f"总第{issue_no}期",
            "issueNo": int(issue_no), "period": period, "periodNo": "",
            "publishDate": pub, "publisher": "青海省建设工程造价站",
            "sourceFile": pdf_path.name, "sourcePages": n_pages,
            "extractedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "extractMethod": "ocr"}
    for i, s in enumerate(sections):
        s["id"] = f"{i:02d}"
        s["label"] = TYPE_LABEL.get(s["kind"], s["kind"])
        s["title"] = f"{s['region']}{period}（第{period_no}期）{s['label']}"
        s["itemCount"] = len(s["rows"])
        s["groups"] = list(dict.fromkeys(r["group"] for r in s["rows"] if r.get("group")))

    payload = {"meta": meta, "issueKey": issue_key, "sections": sections}
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.no_write:            # 分片并行模式：只填 OCR 缓存，最后再统一成篇
        print(f"（分片模式，跳过写盘）缓存 {len(all_lines)} 页")
        return 0
    (out_dir / f"{issue_key}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / f"{issue_key}.js").write_text(
        "window.__ISSUE_DATA__ = window.__ISSUE_DATA__ || {};\n"
        f"window.__ISSUE_DATA__[{json.dumps(issue_key, ensure_ascii=False)}] = "
        + json.dumps(payload, ensure_ascii=False) + ";\n", encoding="utf-8")

    idx_path = out_dir / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {"issues": []}
    idx["issues"] = [x for x in idx["issues"] if x["key"] != issue_key]
    idx["issues"].append({"key": issue_key, "label": f"{period}（总第{issue_no}期）",
                          "period": period, "issueNo": issue_no, "publishDate": pub,
                          "file": f"{issue_key}.json", "js": f"{issue_key}.js",
                          "sections": len(sections),
                          "items": sum(s["itemCount"] for s in sections)})
    idx["issues"].sort(key=lambda x: (str(x["publishDate"]), str(x["issueNo"])))
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "index.js").write_text("window.__ISSUE_INDEX__ = " +
                                      json.dumps(idx, ensure_ascii=False) + ";\n",
                                      encoding="utf-8")

    print(f"完成：{period}（总第{issue_no}期）章节 {len(sections)}，"
          f"条目 {sum(s['itemCount'] for s in sections)}")
    for s in sections:
        pg = s["pages"]
        print(f"  p{pg[0]:>3}-{pg[1]:<3} {s['region']:<7}{s['label']:<12}"
              f"条目 {s['itemCount']:<5} 列: {', '.join(s['areas'][:6])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
