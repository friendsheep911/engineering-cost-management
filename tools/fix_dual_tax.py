# -*- coding: utf-8 -*-
"""双表合并后处理 v3：海西式「含税价格表 + 除税价格表」两张独立表 → 合并进同一行的 excl 槽。

结构特征：section.rows = [表1 全部行] + [表2 全部行]，两表项目清单一致（同序）。

v3 边界检测（关键改进）：不再依赖「同位置同名率」——OCR 断行会给表2 制造重复行，
使表内位置漂移，旧算法的 k 偏移数行导致表头若干行除税整体丢失。现改用「表序重启点」：
表2 首行应与表1 首行（前 3 行内）同名，找 names[k] ≈ names[0..2] 的最小 k，
天然免疫行数漂移；无候选时退回旧的同位置扫描。

对齐：difflib.SequenceMatcher 序列对齐（容两表行拆分不一致），块对齐未覆盖的表1 行
再与剩余表2 行按归一化名称做一对一贪心匹配（二次对齐）。

判定信号（防误配）：
  1. 边界 k 限定在 [n/3, 2n/3]；
  2. 前段与后段含 excl 值的行都 < 5%（真双表两段都是单价格层）；
  3. difflib 对齐匹配率 ≥ 0.75。

用法：python tools/fix_dual_tax.py [--dry]
"""
import json, sys, io, glob, os, re, argparse, difflib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

MIN_ROWS = 16
SIM_MIN = 0.55           # 兜底扫描（同位置同名率）底线
RATIO_MIN = 0.75         # difflib 对齐匹配率底线
EXCL_TOL = 0.05          # 前后段允许含 excl 值的行占比上限
RESTART_SIM = 0.80       # 重启点名称相似底线
SECOND_SIM = 0.62        # 二次贪心对齐相似底线


def _names(rows):
    return [(r.get("name") or "") + "‖" + (r.get("spec") or "") for r in rows]


def _norm(s):
    """名称归一化：去空白、统一 Φ/括号/大小写，供模糊对齐。"""
    s = (s or "").strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace("φ", "Φ").replace("Ф", "Φ")
    s = s.replace("（", "(").replace("）", ")")
    return s.lower()


def _has_excl(r):
    return any((p.get("excl") or {}).get("num") for p in r.get("prices", []))


def _find_boundary(rows, names):
    """返回双表边界 k；找不到返回 None。"""
    n = len(rows)
    lo, hi = max(8, n // 3), min(n - 8, (2 * n) // 3)
    excl = [1 if _has_excl(r) else 0 for r in rows]
    pre = [0]
    for e in excl:
        pre.append(pre[-1] + e)            # pre[k] = rows[:k] 的 excl 行数

    def excl_ok(k):
        m = n - k
        return pre[k] <= EXCL_TOL * k and (pre[n] - pre[k]) <= EXCL_TOL * m

    # ① 表序重启点：表2 开头与表1 开头（前 3 行）同序同名
    heads = [_norm(x) for x in names[:3]]
    for k in range(lo, hi + 1):
        if not excl_ok(k):
            continue
        nk = _norm(names[k])
        if max(difflib.SequenceMatcher(None, nk, h).ratio() for h in heads) >= RESTART_SIM:
            return k
    # ② 兜底：同位置同名率扫描（v1 行为）
    best = (0.0, None)
    for k in range(lo, hi + 1):
        if not excl_ok(k):
            continue
        pairs = min(k, n - k)
        same = sum(1 for i in range(pairs) if names[k + i] == names[i])
        sim = same / pairs
        if sim > best[0]:
            best = (sim, k)
    return best[1] if best[0] >= SIM_MIN else None


def _fill(ra, rb):
    """把表2 行 rb 的价格值填进表1 行 ra 的 excl 槽。"""
    for pr in rb.get("prices", []):
        slot = next((q for q in ra["prices"] if q["area"] == pr["area"]), None)
        if slot is None:
            slot = {"area": pr["area"], "incl": None, "excl": None}
            ra["prices"].append(slot)
        v = pr.get("excl") or pr.get("incl")
        if v and v.get("num") is not None:
            slot["excl"] = v


def detect_and_merge(rows, tag=""):
    """返回 (new_rows, merged?, 边界k, 对齐行数)。"""
    n = len(rows)
    if n < MIN_ROWS:
        return rows, False, None, 0
    names = _names(rows)
    k = _find_boundary(rows, names)
    if k is None:
        return rows, False, None, 0
    a, b = names[:k], names[k:]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    blocks = [x for x in sm.get_matching_blocks() if x.size]
    matched = sum(x.size for x in blocks)
    ratio = matched / max(1, min(len(a), len(b)))
    if ratio < RATIO_MIN:
        print(f"    [跳过] {tag} 边界{k} difflib 匹配率={ratio:.2f} < {RATIO_MIN}")
        return rows, False, None, 0

    used_b = set()
    for bi, bj, size in blocks:
        for t in range(size):
            _fill(rows[bi + t], rows[k + bj + t])
            used_b.add(k + bj + t)
    # 二次对齐：块对齐未覆盖的表1 行 ↔ 剩余表2 行，归一化名称一对一贪心匹配
    match_a = {i for bi, bj, size in blocks for i in range(bi, bi + size)}
    cand_a = [i for i in range(k) if i not in match_a and rows[i].get("prices")]
    cand_b = [j for j in range(k, n) if j not in used_b and rows[j].get("prices")]
    n2 = 0
    if cand_a and cand_b:
        scored = []
        for i in cand_a:
            ni = _norm(names[i])
            for j in cand_b:
                s = difflib.SequenceMatcher(None, ni, _norm(names[j])).ratio()
                if s >= SECOND_SIM:
                    scored.append((s, i, j))
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))
        ta, tb = set(), set()
        for s, i, j in scored:
            if i in ta or j in tb:
                continue
            ta.add(i)
            tb.add(j)
            _fill(rows[i], rows[j])
            n2 += 1
    print(f"    {tag} 边界{k}（{len(a)}+{len(b)} 行）块对齐 {matched} + 二次 {n2} 行")
    return rows[:k], True, k, matched + n2


def write_issue(d, issue_key):
    json_path = os.path.join(DATA, f"{issue_key}.json")
    js_path = os.path.join(DATA, f"{issue_key}.js")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("window.__ISSUE_DATA__ = window.__ISSUE_DATA__ || {};\n"
                f"window.__ISSUE_DATA__[{json.dumps(issue_key, ensure_ascii=False)}] = "
                + json.dumps(d, ensure_ascii=False) + ";\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只检测不写盘")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(DATA, "202*.json")))
    files = [f for f in files
             if not os.path.basename(f).startswith(("index", "official", "_manifest"))]
    total = 0
    for fp in files:
        d = json.load(open(fp, encoding="utf-8"))
        per = d["meta"]["period"]
        issue_key = d["issueKey"]
        changed = False
        for s in d["sections"]:
            rows, merged, k, aligned = detect_and_merge(
                s["rows"], f"{per}|{s['region']}|{s['kind']}")
            if merged:
                n0 = len(s["rows"])
                s["rows"] = rows
                s["itemCount"] = len(rows)
                s["areas"] = list(dict.fromkeys(
                    p["area"] for r in rows for p in r.get("prices", []) if p.get("area")))
                print(f"  {per} [{s['region']}|{s['kind']}] 双表合并：{n0} 行 → {len(rows)} 行")
                changed = True
                total += 1
        if changed and not args.dry:
            write_issue(d, issue_key)
    print(f"=== 完成：共合并 {total} 个 section {'（dry run，未写盘）' if args.dry else ''} ===")


if __name__ == "__main__":
    main()
