# -*- coding: utf-8 -*-
"""材料名称错位纠错（幂等，可反复运行）
========================================
起因：分组标题行丢失时，规格行会继承上一行的材料名称，产生"名称与规格矛盾"的错误行。
实测两类（均由 PDF 原件逐字核对确认）：

  ① 钢筋规格挂到金属材料名下
     - "普通硅酸盐水泥 | HRB400Φ10mm以内"  → 应为 热轧带肋钢筋
     - "中厚钢板 | HPB300Φ6mm"            → 应为 热轧光圆钢筋
     依据：HRB400=热轧带肋钢筋、HPB300=热轧光圆钢筋（国标代号即名称；原件中
     分组标题"热轧带肋钢筋"/"钢筋（圆钢）"因 OCR 合并单元格 y 偏移未落到行上）

  ② 水泥代号与名称矛盾
     - "普通硅酸盐水泥 | P·Ⅱ52.5#（袋）"   → 应为 硅酸盐水泥
     依据：P·Ⅱ 是硅酸盐水泥代号，与"普通硅酸盐水泥"互斥；原件该行名称
     （青海2024年第1期 p102 海晏页）为"硅酸盐水泥"

只改名称与规格**明确矛盾**的行；圆钢/热轧盘条 + HPB300（合法组合）不动。

用法：
    python tools/fix_name_mismatch.py [--dry]
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"

HRB_RE = re.compile(r"HRB\s*400", re.I)
HPB_RE = re.compile(r"HPB\s*300", re.I)


def fix_name(name, spec):
    """返回纠正后的名称；无需纠正时原样返回。"""
    n = (name or "").strip()
    s = (spec or "").replace(" ", "")
    if not s:
        return n
    # ① 钢筋代号挂错名称（"圆钢"是合法材料名，不在此列）
    if HRB_RE.search(s) and not any(k in n for k in ("钢筋", "盘条", "圆钢")):
        return "热轧带肋钢筋"
    if HPB_RE.search(s) and not any(k in n for k in ("钢筋", "盘条", "圆钢", "镀锌")):
        return "热轧光圆钢筋"
    # ② 水泥代号矛盾
    if "普通硅酸盐水泥" in n and ("Ⅱ" in s or "II" in s.upper()):
        return "硅酸盐水泥"
    return n


def main():
    dry = "--dry" in sys.argv
    total, changes = 0, []
    for f in sorted(DATA.glob("202*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[跳过] {f.name}: {e}")
            continue
        per = d.get("meta", {}).get("period") or f.stem
        touched = False
        for sec in d.get("sections", []):
            for i, r in enumerate(sec.get("rows", [])):
                old = r.get("name", "")
                new = fix_name(old, r.get("spec", ""))
                if new != old:
                    changes.append((per, sec.get("region", ""), sec.get("kind", ""),
                                    i, old, r.get("spec", ""), new))
                    if not dry:
                        r["name"] = new
                    touched = True
                    total += 1
        if touched and not dry:
            f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{'[试运行] ' if dry else ''}共纠正 {total} 行：")
    for per, reg, kind, i, old, sp, new in changes:
        print(f"  {per} {reg} {kind} 行{i}: {old!r} + {sp!r} → {new!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
