# -*- coding: utf-8 -*-
"""抽样检查：海西 section 中含税有值但除税缺失的行，定位其页码与行名，供人工对照原刊"""
import json, sys, io, os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

issue = sys.argv[1] if len(sys.argv) > 1 else "2022年第2期"
region = sys.argv[2] if len(sys.argv) > 2 else "海西地区"
kind = sys.argv[3] if len(sys.argv) > 3 else "material"
limit = int(sys.argv[4]) if len(sys.argv) > 4 else 12

d = json.load(open(os.path.join(DATA, issue + ".json"), encoding="utf-8"))
sec = next(s for s in d["sections"] if s["region"] == region and s["kind"] == kind)
print(f"== {issue} [{region}|{kind}] rows={len(sec['rows'])} pages={sec.get('pages')} areas={sec.get('areas')}")

def slots(r):
    for p in r.get("prices", []):
        inc = bool((p.get("incl") or {}).get("num"))
        exc = bool((p.get("excl") or {}).get("num"))
        yield p.get("area", ""), inc, exc

miss_excl = []   # 有含税缺除税
miss_incl = []   # 有除税缺含税（双表未对齐行）
both = 0
for r in sec["rows"]:
    ps = list(slots(r))
    if not ps:
        both += 1
        continue
    me = [(a, (p.get("incl") or {}).get("num")) for p in r.get("prices", [])
          for a, [i, e] in [(p["area"], [bool((p.get("incl") or {}).get("num")), bool((p.get("excl") or {}).get("num"))])] if i and not e]
    mi = [(a, (p.get("excl") or {}).get("num")) for p in r.get("prices", [])
          for a, [i, e] in [(p["area"], [bool((p.get("incl") or {}).get("num")), bool((p.get("excl") or {}).get("num"))])] if e and not i]
    if me:
        miss_excl.append((r, me))
    if mi:
        miss_incl.append((r, mi))

print(f"\n-- 有含税缺除税：{len(miss_excl)} 行（示例前 {limit} 行）")
for r, me in miss_excl[:limit]:
    print(f"  {r['name'][:24]!r} {r.get('spec','')[:14]!r} 缺除税县: {[(a, v) for a, v in me][:8]}")
print(f"\n-- 有除税缺含税（双表对齐残留行）：{len(miss_incl)} 行")
for r, mi in miss_incl[:limit]:
    print(f"  {r['name'][:24]!r} {r.get('spec','')[:14]!r} 缺含税县: {[(a, v) for a, v in mi][:8]}")
print(f"\n-- 无价格行: {both}")
