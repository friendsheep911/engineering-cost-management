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

print("\n=== DONE ===")
