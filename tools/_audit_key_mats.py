# -*- coding: utf-8 -*-
"""重点材料筛查：覆盖矩阵 + spec 污染 + 序列碎片"""
import json, re, sys, io, glob
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"

KEY_MATS = ["普通硅酸盐水泥", "复合硅酸盐水泥", "硅酸盐水泥", "抗硫水泥",
            "热轧带肋钢筋", "热轧光圆钢筋", "钢筋",
            "净砂", "中砂", "砂砾石", "碎石", "卵石",
            "预拌混凝土", "商品混凝土", "柴油", "汽油", "粉煤灰"]

def norm(s):
    return (s or "").replace("＃", "#").replace("（", "(").replace("）", ")").replace(" ", "").strip()

# ---------- 1. spec 污染行（钢筋代号但名称不含钢筋）----------
print("========== ① spec 钢筋代号但名称不符（名称错位污染） ==========")
bad = {}
for f in sorted(glob.glob(str(DATA / "202*.json"))):
    d = json.loads(Path(f).read_text(encoding="utf-8"))
    per = d["meta"]["period"]
    for s in d["sections"]:
        for r in s.get("rows", []):
            sp = r.get("spec") or ""
            nm = r.get("name") or ""
            if re.search(r"HRB\d|HPB\d", sp) and "钢筋" not in nm:
                bad.setdefault((nm, norm(sp)), []).append(per)
for (nm, sp), pers in sorted(bad.items()):
    print(f"  name={nm!r} spec={sp!r}  {len(pers)}期: {pers[0]}~{pers[-1]}")

# ---------- 2. 重点材料归一后 key 的序列碎片度 ----------
print("\n========== ② 重点材料：归一 key (name,强度,包装) 的覆盖 ==========")
def key_of(nm, sp):
    s = norm(sp)
    nm2 = norm(nm)
    pack = None
    m = re.search(r"\((散|袋)\)", s) or re.search(r"\((散|袋)\)", nm2)
    if m: pack = m.group(1)
    g = re.search(r"(32\.5|42\.5|52\.5|62\.5)", s)
    grade = g.group(1) if g else None
    return (nm2, grade, pack)

def label(p): return p.replace("年第", "-").replace("期", "")

for kw in KEY_MATS:
    cov = {}   # key -> set(period_idx)
    variants = {}
    idxs = []
    for f in sorted(glob.glob(str(DATA / "202*.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        pi = len(idxs); idxs.append(d["meta"]["period"])
        for s in d["sections"]:
            for r in s.get("rows", []):
                nm = r.get("name") or ""
                if kw not in nm:
                    continue
                k = key_of(nm, r.get("spec"))
                cov.setdefault(k, set()).add(pi)
                variants.setdefault(k, set()).add(norm(r.get("spec")))
    if not cov:
        print(f"  [{kw}] 无数据")
        continue
    print(f"  [{kw}] 归一后 {len(cov)} 条序列:")
    for k in sorted(cov, key=lambda x: -len(cov[x])):
        ps = sorted(cov[k])
        # 找断档
        gaps = []
        for a, b in zip(ps, ps[1:]):
            if b - a > 1:
                gaps.append(f"{label(idxs[a+1])}~{label(idxs[b-1])})缺")
        v = "; ".join(sorted(variants[k])[:4])
        print(f"    {k[0]}|{k[1]}|{k[2]}  {len(ps)}期 {label(idxs[ps[0]])}~{label(idxs[ps[-1]])}"
              + (f"  断档:{','.join(gaps)}" if gaps else "")
              + f"  变体[{len(variants[k])}]: {v}")
