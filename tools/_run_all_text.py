# -*- coding: utf-8 -*-
"""临时脚本：批量重抽全部文本期（跳过 _ocr_cache 中的 OCR 期）。"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT.parent / "00_原始资料" / "信息价"
OUT = ROOT / "data"
PY = sys.executable

ocr_skip = {p.name for p in (OUT / "_ocr_cache").iterdir()} | \
           {p.name + ".pdf" for p in (OUT / "_ocr_cache").iterdir()}
pdfs = sorted(PDF_DIR.glob("青海工程造价管理信息_*.pdf"))
print(f"共 {len(pdfs)} 个 PDF，OCR 期跳过 {len(ocr_skip)} 个", flush=True)

fail = []
DONE = set()   # 已人工确认完成的期号可临时加入（如 {"2020", "2022年第3期"}）以断点续跑
for pdf in pdfs:
    if pdf.name in ocr_skip:
        print(f"[跳过 OCR 期] {pdf.name}", flush=True)
        continue
    m = re.search(r"(\d{4})年第(\d+)期", pdf.name)
    if not m:
        print(f"[跳过 无法解析期号] {pdf.name}", flush=True)
        continue
    key = f"{m.group(1)}年第{m.group(2)}期"
    if key[:4] in DONE or key in DONE:
        print(f"[跳过 已完成] {key}", flush=True)
        continue
    print(f"[抽取] {key} ...", flush=True)
    r = subprocess.run([PY, str(ROOT / "tools" / "extract_info_price.py"),
                        str(pdf), "--out", str(OUT)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (r.stdout or "").strip().splitlines()
    keyline = next((x for x in tail if "价格条目" in x), "?")
    n_issue = next((x for x in tail if x.strip().startswith("提示")), "?")
    if r.returncode != 0 or "价格条目" not in (r.stdout or ""):
        fail.append(key)
        print(f"    !! 失败 rc={r.returncode} {keyline}", flush=True)
        err = (r.stderr or "").strip().splitlines()
        for x in err[-3:]:
            print(f"    {x}", flush=True)
    else:
        print(f"    ok {keyline}  {n_issue}", flush=True)

print(f"\n完成，失败 {len(fail)} 期: {fail}", flush=True)
