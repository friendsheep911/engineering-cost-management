# -*- coding: utf-8 -*-
"""终检：全期 areas 伪列扫描 + Bug⑩/⑧/⑨ 专项验证。

检查项：
A. 40 期所有 section：prices 中出现含「地区」的 area（伪列）→ 报告
B. Bug⑩ 专项：2020-2 海西 material 泵送预拌混凝土各强度等级应 7 县完整
   （德令哈/格尔木/茫崖/都兰/乌兰/天峻/大柴旦行委）
C. Bug⑨ 专项：2024-1/2/6 海西 material 列应为 7 县而非「海西地区」
D. Bug⑧ 回归：2024-3+ 文本期海西 7 县
"""
import json, os, glob, sys, io
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

files = sorted(glob.glob(os.path.join(DATA, "202*.json")))
files = [f for f in files if not os.path.basename(f).startswith(("index", "official", "_manifest"))]
print(f"共 {len(files)} 期 json")


def count_disjoint(rows):
    """同一 (名称,型号,单位) 的行，若存在两个地区集合互不相交 → 典型的「平行表未合并」特征。
    （正常形态下同一材料的不同分区集合应是包含/相等关系，如"部分县无价"是子集。）"""
    g = {}
    for r in rows:
        areas = frozenset(p["area"] for p in r.get("prices", []) if p.get("area"))
        if not areas:
            continue
        k = (r.get("name", ""), r.get("spec", ""), r.get("unit", ""))
        g.setdefault(k, set()).add(areas)
    n = 0
    for k, ss in g.items():
        ss = list(ss)
        if any(ss[i].isdisjoint(ss[j]) for i in range(len(ss)) for j in range(i + 1, len(ss))):
            n += 1
    return n

bad_a = []          # A: 伪地区列（仅两种真伪列形态）
for f in files:
    d = json.load(open(f, encoding="utf-8"))
    per = d["meta"]["period"]
    for s in d["sections"]:
        reg = s["region"].replace("地区", "")
        p_areas = set()
        for r in s["rows"]:
            for p in r.get("prices", []):
                a = p.get("area", "")
                if a:
                    p_areas.add(a)
        hdr_set = set(s.get("areas", []))
        all_areas = p_areas | hdr_set
        region_col = [a for a in all_areas if "地区" in a]
        county_cols = [a for a in all_areas if "地区" not in a]
        # 伪列形态1：海西多县表出现「海西地区」列（海西自 2020 起全部为县列表）
        if reg == "海西" and region_col:
            bad_a.append((per, s["region"], s["kind"], f"海西伪列{region_col}"))
        # 伪列形态2：「XX地区」列与县名列在同一 section 共存（塌缩特征）
        elif region_col and county_cols:
            bad_a.append((per, s["region"], s["kind"],
                          f"地区列{region_col}与县列{county_cols[:6]}共存"))

if bad_a:
    print(f"\n[A] 伪地区列残留 {len(bad_a)} 处:")
    for x in bad_a[:40]:
        print("   ", x)
else:
    print("[A] 伪地区列: 全部清除 ✓")


def haixi_material(per_file_kw):
    """找 海西 material section，返回 (期名, areas, 泵送行值摘要)。"""
    for f in files:
        base = os.path.basename(f)
        if per_file_kw not in base:
            continue
        d = json.load(open(f, encoding="utf-8"))
        for s in d["sections"]:
            if s["region"].replace("地区", "") == "海西" and s["kind"] == "material":
                return base, s
    return per_file_kw, None


print("\n[B] Bug⑩ 专项 2020-2 海西 material:")
f2, s2 = haixi_material("2020年第2期")
if s2 is None:
    print("    !! 未找到海西 material section")
else:
    areas = s2.get("areas", [])
    print(f"    {f2} areas={areas}")
    pump = [r for r in s2["rows"] if "泵送预拌混凝土" in r.get("name", "")]
    print(f"    泵送预拌混凝土行数={len(pump)} (期望 9~10 个强度等级)")
    for r in pump[:3]:
        ps = {p["area"]: (p.get("incl") or {}).get("num") for p in r["prices"]}
        print(f"      {r['name']} {r.get('spec','')}: {ps}")

print("\n[C] Bug⑨ 专项 2024 OCR 期海西 material 表头:")
for kw in ("2024年第1期", "2024年第2期", "2024年第6期"):
    f, s = haixi_material(kw)
    if s is None:
        print(f"    {kw} !! 未找到海西 material")
        continue
    print(f"    {kw} areas={s.get('areas', [])}")

print("\n[D] Bug⑧ 回归 2024-3+/2025 文本期海西 material 表头:")
for kw in ("2024年第3期", "2024年第4期", "2025年第1期", "2025年第2期"):
    f, s = haixi_material(kw)
    if s is None:
        print(f"    {kw} (无海西material或无该期)")
        continue
    print(f"    {kw} areas={s.get('areas', [])}")

print("\n[E] 平行双表配对回归（同一材料的多个行段，地区集合必须一致）:")
hanan = []
for f in files:
    d = json.load(open(f, encoding="utf-8"))
    for s in d["sections"]:
        if s["region"].replace("地区", "") != "黄南" or s["kind"] != "material":
            continue
        nbad = count_disjoint(s["rows"])
        ua = sorted({p["area"] for r in s["rows"] for p in r.get("prices", []) if p.get("area")})
        hanan.append((os.path.basename(f), len(s["rows"]), nbad, ",".join(ua)))
bad_e = [x for x in hanan if x[2] > 0]
if bad_e:
    print(f"    !! {len(bad_e)} 期黄南 material 存在「同材料地区集合不一致」（拆分未合并）:")
    for b, n, nd, _ in bad_e:
        print(f"      {b} 行{n} 异常材料 {nd} 项")
else:
    print(f"    {len(hanan)} 期黄南 material 全部通过 ✓（同一材料地区集合唯一）")
    for b, n, nd, areas in hanan[-3:]:
        print(f"      {b} 行{n} 列={areas}")

print("\n[F] 全期全地区扫描：同材料行段地区集合不一致（平行表未合并的通用特征）:")
allbad = []
for f in files:
    d = json.load(open(f, encoding="utf-8"))
    for s in d["sections"]:
        nd = count_disjoint(s["rows"])
        if nd >= 1:
            allbad.append((d["meta"]["period"], s["region"], s["kind"], len(s["rows"]), nd))
if allbad:
    print(f"    需甄别 {len(allbad)} 个 section:")
    for per, reg, kind, n, nd in allbad[:20]:
        print(f"      {per} {reg} {kind} 行{n} 异常{nd}")
else:
    print("    无 ✓")

fail = bool(bad_a or bad_e or allbad)   # A/E/F 为硬门禁；B/C/D 仅信息性
print(f"\n=== DONE {'FAIL' if fail else 'PASS'} ===")
sys.exit(1 if fail else 0)
