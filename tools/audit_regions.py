# -*- coding: utf-8 -*-
"""全量体检：每期各 section 的县覆盖 + incl/excl 完整性"""
import json, sys, io, glob, os
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

files = sorted(glob.glob(os.path.join(DATA, "202*.json")))
files = [f for f in files if not os.path.basename(f).startswith(("index", "official", "_manifest"))]

for f in files:
    d = json.load(open(f, encoding="utf-8"))
    per = d["meta"]["period"]
    method = d["meta"].get("extractMethod", "text")
    issues = []
    for s in d["sections"]:
        reg = s["region"]
        areas_hdr = list(s.get("areas", []))
        # prices 里实际出现的 area 与 incl/excl 缺失
        area_cnt = Counter()
        miss_incl = miss_excl = miss_both = 0
        for r in s["rows"]:
            ps = r.get("prices", [])
            if not ps:
                miss_both += 1
                continue
            for p in ps:
                a = p.get("area", "")
                area_cnt[a] += 1
                inc = p.get("incl") or {}
                exc = p.get("excl") or {}
                has_i = bool(inc.get("num"))
                has_e = bool(exc.get("num"))
                if not has_i and not has_e:
                    miss_both += 1
                elif not has_i:
                    miss_incl += 1
                elif not has_e:
                    miss_excl += 1
        hdr_set = set(a.replace(" ", "") for a in areas_hdr)
        price_set = set(area_cnt.keys())
        only_hdr = hdr_set - price_set   # 表头有县但行里没价格
        only_price = price_set - hdr_set # 行里有价格但表头没县
        n_rows = len(s["rows"])
        # 只记录异常 section
        flags = []
        if only_hdr: flags.append(f"表头县无价格:{sorted(only_hdr)}")
        if only_price: flags.append(f"价格县不在表头:{sorted(only_price)}")
        if miss_both > n_rows * 0.05: flags.append(f"无价格行={miss_both}/{n_rows}")
        if miss_incl > 0: flags.append(f"缺含税={miss_incl}")
        if miss_excl > 0: flags.append(f"缺除税={miss_excl}")
        if flags:
            issues.append(f"  [{reg}|{s['kind']}] rows={n_rows} 县表头={len(areas_hdr)} " + " ".join(flags))
    n_rows_all = sum(len(s["rows"]) for s in d["sections"])
    if issues:
        print(f"== {per} ({method}) rows={n_rows_all} sections={len(d['sections'])}")
        print("\n".join(issues))
print("=== DONE ===")
