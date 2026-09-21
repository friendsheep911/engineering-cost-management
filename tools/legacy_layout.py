# -*- coding: utf-8 -*-
"""旧版式（横向双栏 / 压缩单元格）材料表解析。

2019~2023 年刊物为横排 A4，表格分两种形态：
  压缩型: 表格被识别为 2~6 个巨行，数据以换行挤在单元格内（名称列还存在
          跨行合并），需按词坐标重建行带。
  网格型: 正常网格，但 A 页(名称+前几县) 与 B 页(后几县+备注) 分属两页，
          行数相等、按行横向拼接。

对外的输出统一为"逻辑行"结构，直接供 extract_info_price 组装章节：
  cols:  [{role, area, ptype}]     role ∈ name/spec/unit/note/price
  bands: [[col_text, ...], ...]    与 cols 对齐的行
"""
import re
from collections import defaultdict

NUM_RE = re.compile(r"^-?[\d,.]+%?$")
DASH_RE = re.compile(r"^[-—－―‐–.]+$")   # 表内"—"表示无价，但占一个行位


def is_compressed(rows):
    n = 0
    for r in rows:
        for c in r:
            if c and c.count("\n") >= 4:
                n += 1
    return n >= 3


def clean(s):
    return re.sub(r"\s+", "", s or "")


# ---------------------------------------------------------------- 压缩型
def parse_compressed(page, tb):
    """按词坐标重建行带。返回 (cols, rows) 或 None。
    cols: [{role, area, ptype}]；rows: 与 cols 对齐的字符串矩阵（含表头之上的空行不会出现）。"""
    words = page.get_text("words")
    if not tb.cells:
        return None
    n_cols = len(tb.extract()[0])

    # 列边界：由表格 cells 的 x 值聚类
    xs = []
    for (x0, y0, x1, y1) in tb.cells:
        xs.extend([x0, x1])
    xs.sort()
    segs = []
    for x in xs:
        if not segs or x - segs[-1] > 3:
            segs.append(x)
    if len(segs) < n_cols + 1:          # 边界不足时按等分兜底
        x0 = min(c[0] for c in tb.cells)
        x1 = max(c[2] for c in tb.cells)
        step = (x1 - x0) / n_cols
        segs = [x0 + i * step for i in range(n_cols + 1)]
        segs[-1] = x1 + 1
    segs[-1] += 5                        # 右边界容差

    # 词按 (block, line) 组成文本行
    groups = defaultdict(list)
    ys_all = [c[1] for c in tb.cells] + [c[3] for c in tb.cells]
    top, bot = min(ys_all), max(ys_all)
    for x0, y0, x1, y1, txt, blk, ln, wno in words:
        if y1 < top - 2 or y0 > bot + 2:
            continue
        groups[(blk, ln)].append((x0, y0, x1, y1, txt))
    lines = []
    for ws in groups.values():
        ws.sort(key=lambda t: t[0])
        y = sum(t[1] + t[3] for t in ws) / (2 * len(ws))
        lines.append((y, ws[0][0], " ".join(t[4] for t in ws)))

    # 行 -> 列
    col_lines = [[] for _ in range(n_cols)]
    for y, x, text in lines:
        for i in range(n_cols):
            if segs[i] - 2 <= x < segs[i + 1]:
                col_lines[i].append((y, clean(text)))
                break

    # 列内双价格拼接拆分：如 2020-2 海西泵送混凝土段，乌兰价带「(茶卡398)」
    # 括注，同一印刷行的两个价格词同 (blk,ln) 拼成一行且行首 x 落在左价格列
    # （"350.00350(茶卡398)"）。按「价格+带括注价格」特征拆开，主价归还右邻
    # 列；括注为镇级参考价（模型无法表达），剥除。
    # group(1) 限定「整数部分+两位小数」完整价格形态，避免贪婪回溯错拆：
    # '350.00350(茶卡398)' → '350.00' + '350(茶卡398)'；'60.00（茶卡）'
    # （括注内无价）不匹配，不误拆。
    SPLIT2_RE = re.compile(r"^(\d[\d,]*\.\d{2})(\d+[(（][^)）]*[)）])$")
    for i in range(n_cols - 1):
        out, extra, hit = [], [], False
        for y, t in col_lines[i]:
            m = SPLIT2_RE.match(t)
            # 右列该行已有数字则不拆（拼接未发生，防止破坏正常行）
            if m and any(NUM_RE.match(rt) for ry, rt in col_lines[i + 1]
                         if abs(ry - y) < 3):
                m = None
            if m:
                hit = True
                out.append((y, m.group(1)))
                m2 = re.match(r"^[\d,.]+", m.group(2))
                extra.append((y, m2.group(0)))
            else:
                out.append((y, t))
        if hit:
            col_lines[i] = out
            col_lines[i + 1].extend(extra)
            col_lines[i + 1].sort()

    # 每列的数值行 y（供角色判定与行带生成）
    col_num_ys = [[y for y, t in col_lines[i] if NUM_RE.match(t)]
                  for i in range(n_cols)]

    # 列角色第一遍：关键字判定（表头 = 该列自己首个数值行之上的文本）
    cols = []
    for i in range(n_cols):
        own_first = col_num_ys[i][0] if col_num_ys[i] else None
        cut = (own_first - 2) if own_first is not None else None
        # hs 过滤含数字的行：数据价格若因拼接/错位落入表头区，会把
        # joined 污染成 "德令哈350.00…"，_area_ok 拒绝后 area=None →
        # 塌缩成地区伪列（2020-2~2022-3 海西「海西地区」列成因）
        hs = [t for y, t in col_lines[i]
              if cut is not None and y < cut and t and not re.search(r"\d", t)]
        joined = clean("".join(hs))
        if "材料名称" in joined or "机械名称" in joined or "工种名称" in joined or "项目名称" in joined:
            cols.append({"role": "name", "area": None, "ptype": None})
        elif "规格" in joined or "强度等级" in joined:
            cols.append({"role": "spec", "area": None, "ptype": None})
        elif "单位" in joined:
            cols.append({"role": "unit", "area": None, "ptype": None})
        elif "备注" in joined:
            cols.append({"role": "note", "area": None, "ptype": None})
        elif any("含税" in h for h in hs):
            cols.append({"role": "price", "area": _area_of(hs), "ptype": "incl"})
        elif any("除税" in h for h in hs):
            cols.append({"role": "price", "area": _area_of(hs), "ptype": "excl"})
        elif any("价格" in h or "单价" in h for h in hs):
            cols.append({"role": "price", "area": _area_of(hs), "ptype": "incl"})
        else:
            # 无关键字表头（如海西单价格层：表头即县名）——留待第二遍
            cols.append({"role": "?", "area": joined[:10] or None, "ptype": None,
                         "_nums": len(col_num_ys[i])})

    # 列角色第二遍：无关键字列 —— 数值为主→价格列（先于行带生成）
    for i, c in enumerate(cols):
        if c["role"] != "?":
            continue
        nums = c.pop("_nums", 0)
        joined = c["area"] or ""
        if nums >= 3 and 0 < len(joined) <= 10:
            c.update({"role": "price", "area": _area_ok(joined), "ptype": "incl"})

    # 行带候选：价格列的数值/"—"行（"—"无价但占行位，保证 A/B 行带对应）
    def band_marker(t):
        return bool(NUM_RE.match(t) or DASH_RE.match(t))

    def collect_price_ys():
        return sorted(y for i, c in enumerate(cols)
                      if c["role"] == "price"
                      for y, t in col_lines[i] if band_marker(t))

    price_ys = collect_price_ys()
    if not price_ys:
        # 极端情况：整页价格全是"—"或角色全未识别 —— 用全局首个数值行切表头再判一遍
        all_ys = sorted(y for i in range(n_cols) for y in col_num_ys[i])
        if not all_ys:
            return None
        prov = all_ys[0]
        for i, c in enumerate(cols):
            if c["role"] != "?":
                continue
            hs = [t for y, t in col_lines[i]
                  if y < prov - 2 and t and not re.search(r"\d", t)]
            if any("含税" in h for h in hs):
                c.update({"role": "price", "area": _area_of(hs), "ptype": "incl"})
            elif any("除税" in h for h in hs):
                c.update({"role": "price", "area": _area_of(hs), "ptype": "excl"})
            elif any("价格" in h or "单价" in h for h in hs):
                c.update({"role": "price", "area": _area_of(hs), "ptype": "incl"})
        price_ys = collect_price_ys()
        if not price_ys:
            return None
    first_num_y = price_ys[0]
    bands = []
    for y in price_ys:
        if bands and y - bands[-1][-1] < 4:
            bands[-1].append(y)
        else:
            bands.append([y])
    if len(bands) < 5:
        return None
    band_y = [sum(b) / len(b) for b in bands]

    # 列角色第三遍：仍无角色且无数值的列，用全局首个价格行切割表头再判关键字
    for i, c in enumerate(cols):
        if c["role"] != "?":
            continue
        cut = first_num_y - 2
        hs = [t for y, t in col_lines[i] if y < cut and t]
        joined = clean("".join(hs))
        if "材料名称" in joined or "机械名称" in joined or "工种名称" in joined:
            c.update({"role": "name", "area": None, "ptype": None})
        elif "规格" in joined or "强度等级" in joined:
            c.update({"role": "spec", "area": None, "ptype": None})
        elif "单位" in joined:
            c.update({"role": "unit", "area": None, "ptype": None})
        elif "备注" in joined:
            c.update({"role": "note", "area": None, "ptype": None})
        elif i == 0 and not joined:
            c.update({"role": "name", "area": None, "ptype": None})

    price_idx = [i for i, c in enumerate(cols) if c["role"] == "price"]
    if not price_idx:
        return None
    # 县名继承：除税列 / 无表头价格列，继承左侧最近的县名
    cur = None
    for c in cols:
        if c["role"] == "price":
            if c["area"]:
                cur = c["area"]
            else:
                c["area"] = cur
        elif c["role"] == "name":
            cur = None

    # 数据行
    rows = []
    for by in band_y:
        row = []
        for i, c in enumerate(cols):
            if c["role"] == "price":
                # 该 y 附近的数值或"—"
                cand = [t for y, t in col_lines[i]
                        if abs(y - by) < 5 and band_marker(t)]
                row.append(cand[0] if cand else "")
            else:
                cand = [t for y, t in col_lines[i] if abs(y - by) < 5 and t]
                row.append(cand[0] if cand else None)   # None=需向前继承
        rows.append(row)
    # 文本列向前继承
    last = {}
    for r in rows:
        for i, c in enumerate(cols):
            if c["role"] != "price":
                if r[i] is None:
                    r[i] = last.get(i, "")
                else:
                    last[i] = r[i]

    # 分组行：名称列中不邻接任何行带的文本行（如"黑色金属"分类行）
    # 表头下界 = 最后一个含表头关键字的文本行 y
    HDR_KW = ("材料名称", "型号规格", "强度等级", "机械名称", "工种名称", "单位",
              "含税", "除税", "备注", "（元）", "价格")
    hdr_ys = [y for i in range(n_cols) for y, t in col_lines[i]
              if y < first_num_y + 6 and any(k in t for k in HDR_KW)]
    hdr_bottom = (max(hdr_ys) + 3) if hdr_ys else band_y[0] - 2
    name_i = next((i for i, c in enumerate(cols) if c["role"] == "name"), None)
    groups = []
    if name_i is not None:
        for y, t in col_lines[name_i]:
            if not t or y < hdr_bottom:
                continue
            if all(abs(y - by) >= 5 for by in band_y):
                groups.append((y, t))
        groups.sort()
    return {"cols": cols, "rows": rows, "band_y": band_y, "groups": groups}


_BAD_AREA = ("元", "价格", "税", "单位", "名称", "规格", "备注", "序号",
             "合计", "小计", "年", "期", "月份")


def _area_ok(t):
    """过滤表头碎片（"（元）"、"单位含税价格" 之类）被当作地区名的情况。
    保留 "海晏县（含西海镇）" 这类带括注的真实地名。"""
    t = (t or "").strip()
    if not t or len(t) > 12:
        return None
    if t[0] in "（(":                       # 以括号开头 = 表头残余
        return None
    if any(k in t for k in _BAD_AREA):
        return None
    if re.search(r"\d", t):
        return None
    return t


def _area_of(hs):
    for h in hs:
        t = clean(h)
        if t and not ("含税" in t or "除税" in t or "元" in t or "价格" in t):
            return _area_ok(t)
    return None


def compress_items(parsed, groups=None, band_y=None):
    """把 parse_compressed 的结果转成条目列表。
    groups: [(y, text)] 分组行；band_y: 各数据行 y（用于把分组归属到行）。"""
    cols, rows = parsed["cols"], parsed["rows"]
    band_y = band_y or parsed.get("band_y") or []
    groups = groups if groups is not None else parsed.get("groups", [])
    items = []
    for bi, r in enumerate(rows):
        by = band_y[bi] if bi < len(band_y) else 0
        grp = ""
        for gy, gt in groups:
            if gy < by - 2:
                grp = gt
        d = {}
        for i, c in enumerate(cols):
            d[c["role"]] = r[i] or ""
        prices = []
        for i, c in enumerate(cols):
            if c["role"] == "price" and r[i]:
                prices.append({"area": c["area"], "ptype": c["ptype"], "raw": r[i]})
        items.append({"name": d.get("name") or "", "spec": d.get("spec") or "",
                      "unit": d.get("unit") or "", "note": d.get("note") or "",
                      "group": grp, "prices": prices})
    return items


def merge_pair(a, b):
    """A/B 两半页拼接（parse_compressed 的返回值）。返回合并后的 parsed。"""
    acols, arows, aby = a["cols"], a["rows"], a["band_y"]
    bcols, brows, bby = b["cols"], b["rows"], b["band_y"]

    def priced_idx(rows, cols):
        return [i for i, r in enumerate(rows)
                if any(r[j] for j, c in enumerate(cols) if c["role"] == "price")]
    api, bpi = priced_idx(arows, acols), priced_idx(brows, bcols)
    if len(api) != len(bpi):
        return None
    cols = list(acols) + [c for c in bcols if c["role"] in ("price", "note")]
    rows = []
    for ia, ib in zip(api, bpi):
        rows.append(list(arows[ia]) + [brows[ib][j] for j, c in enumerate(bcols)
                                       if c["role"] in ("price", "note")])
    band_y = [aby[i] for i in api]
    return {"cols": cols, "rows": rows, "band_y": band_y, "groups": a.get("groups", [])}


# ---------------------------------------------------------------- 网格型 A/B
def grid_pair(a_rows, b_rows):
    """2023-6 式：A/B 均为正常网格，行一一对应。返回 (cols, rows) 或 None。"""
    if len(a_rows) != len(b_rows) or len(a_rows) < 6:
        return None
    ah, bh = a_rows[0], b_rows[0]
    bh0 = clean(bh[0])
    if any(k in bh0 for k in ("材料", "名称", "序号", "机械", "工种", "项目")):
        return None                                  # B 首格仍是名称列 -> 不是 B 页
    if not all(clean(c) for c in bh[:-1]):
        return None                                  # B 表头应为连续县名
    # A 表头定位
    def roles(h):
        out = []
        for c in h:
            t = clean(c)
            if "材料名称" in t or "机械名称" in t or "工种名称" in t:
                out.append({"role": "name", "area": None, "ptype": None})
            elif "规格" in t or "强度等级" in t:
                out.append({"role": "spec", "area": None, "ptype": None})
            elif "单位" in t:
                out.append({"role": "unit", "area": None, "ptype": None})
            elif "备注" in t:
                out.append({"role": "note", "area": None, "ptype": None})
            else:
                out.append({"role": "price", "area": t, "ptype": "incl"})
        return out
    acols = roles(ah)
    bcols = roles(bh)
    if not any(c["role"] == "name" for c in acols):
        return None
    cols = acols + bcols
    rows = [list(ra) + list(rb) for ra, rb in zip(a_rows[1:], b_rows[1:])]
    return cols, rows


def grid_pair_nn(a_rows, b_rows):
    """双名称列网格配对（2020 黄南/玉树式：左右两个半页表均带名称列，
    3 行表头 = 标签行 / 县名行 / 含税除税行）。返回 (cols, rows) 或 None。"""
    if len(a_rows) != len(b_rows) or len(a_rows) < 6:
        return None

    def counties_of(rows):
        # 县名行 = 含税/除税行的上一行；找不到则用首行
        cy = next((i for i, r in enumerate(rows[:4])
                   if any("含税" in clean(c) or "除税" in clean(c) for c in r)), None)
        if cy is None or cy == 0:
            return set(), cy
        return {clean(c) for c in rows[cy - 1] if clean(c)}, cy

    acounties, acy = counties_of(a_rows)
    bcounties, bcy = counties_of(b_rows)
    if not acounties or not bcounties or acy != bcy:
        return None
    if acounties & bcounties:                     # 县名重叠 -> 不是同一表的两半
        return None

    def build(rows, skip_head):
        return _grid_cols(rows, acy, drop_labels=skip_head)

    acols = build(a_rows, skip_head=False)
    bcols = build(b_rows, skip_head=True)
    if not acols or not bcols:
        return None
    body_a = a_rows[acy + 1:]
    body_b = [r[3:] for r in b_rows[acy + 1:]]
    cols = acols + bcols
    rows = [list(ra) + list(rb) for ra, rb in zip(body_a, body_b)]
    return cols, rows


def _grid_cols(rows, hdr_idx, drop_labels):
    """3 行表头的列角色：标签行 / 县名行(合并单元格需向后继承) / 含税除税行。
    drop_labels=True 时丢弃 名称/规格/单位 三列（右半页 B 表用）。"""
    head = rows[0]
    cy_row = rows[hdr_idx - 1]
    pt_row = rows[hdr_idx]
    cols, cur_county = [], None
    for i in range(len(head)):
        t = clean(cy_row[i]) if i < len(cy_row) else ""
        pt = clean(pt_row[i]) if i < len(pt_row) else ""
        ht = clean(head[i]) if i < len(head) else ""
        if t:
            cur_county = t
        if not t and not pt:
            # 非价格列：按标签行判定（县名/含税除税都为空）
            if "备注" in ht:
                cols.append({"role": "note", "area": None, "ptype": None})
            elif drop_labels:
                continue
            elif "名称" in ht:
                cols.append({"role": "name", "area": None, "ptype": None})
            elif "规格" in ht or "强度等级" in ht:
                cols.append({"role": "spec", "area": None, "ptype": None})
            elif "单位" in ht:
                cols.append({"role": "unit", "area": None, "ptype": None})
            else:
                cols.append({"role": "note", "area": None, "ptype": None})
            continue
        cols.append({"role": "price", "area": cur_county,
                     "ptype": "excl" if "除税" in pt else "incl"})
    return cols


def grid_standalone(rows):
    """单独一张多县网格表（黄南/玉树式：名称列 + 多个县名含税/除税列）。
    返回 (cols, rows)；县数不足 2 或缺名称列则 None。"""
    if len(rows) < 6:
        return None
    cy = next((i for i, r in enumerate(rows[:4])
               if any("含税" in clean(c) or "除税" in clean(c) for c in r)), None)
    if cy is None or cy == 0:
        return None
    counties = {clean(c) for c in rows[cy - 1] if clean(c)}
    if len(counties) < 2:
        return None
    cols = _grid_cols(rows, cy, drop_labels=False)
    if not any(c["role"] == "name" for c in cols):
        return None
    if sum(1 for c in cols if c["role"] == "price") < 2:
        return None
    return cols, rows[cy + 1:]
