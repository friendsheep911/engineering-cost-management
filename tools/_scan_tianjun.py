# -*- coding: utf-8 -*-
"""扫描全部 json 中 areas/price.area 含「天骏」的期。"""
import json, os, glob, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
files = sorted(glob.glob(os.path.join(DATA, "202*.json")))
files = [f for f in files if not os.path.basename(f).startswith(("index", "official", "_manifest"))]

hits = []
for f in files:
    d = json.load(open(f, encoding="utf-8"))
    per = d["meta"]["period"]
    for s in d["sections"]:
        hdr = [a for a in s.get("areas", []) if "天骏" in a]
        row_hits = 0
        for r in s["rows"]:
            for p in r.get("prices", []):
                if "天骏" in p.get("area", ""):
                    row_hits += 1
        if hdr or row_hits:
            hits.append((per, s["region"], s["kind"], hdr, row_hits))

if hits:
    print(f"「天骏」残留 {len(hits)} 个 section:")
    for h in hits:
        print("  ", h)
else:
    print("无「天骏」残留 ✓")
