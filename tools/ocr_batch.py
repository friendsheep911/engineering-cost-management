# -*- coding: utf-8 -*-
"""
转曲期批量 OCR 并行驱动
========================
对多期"文字转曲"PDF 并行调用 ocr_extract.py（各期独立、互不依赖）。
用法：
    python tools/ocr_batch.py <PDF目录> --out data --workers 5 [--only 2022年第4期,2022年第5期]
输出汇总：各期 章节数 / 条目数 / 失败原因。
"""
import argparse
import subprocess
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 已装 RapidOCR 的隔离环境解释器（与主解释器同一 Python 版本）
VENV_PY = Path(os.environ.get(
    "OCR_PY", r"C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"))


def run_one(pdf, out, dpi, threads, extra=()):
    env = dict(os.environ)
    # 每个子进程只占 1 个推理线程，靠"多进程"吃掉多核；否则 ORT 会用满物理核互相抢
    env["OCR_INTRA_THREADS"] = str(threads)
    env.setdefault("OMP_NUM_THREADS", str(threads))
    env["OCR_NO_CLS"] = "1"          # 表内文字全是横排，方向分类纯浪费
    env["OCR_REC_BATCH"] = "16"
    cmd = [str(VENV_PY), "-u", str(HERE / "ocr_extract.py"), str(pdf),
           "--out", str(out), "--dpi", str(dpi)] + list(extra)
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    tail = (r.stdout or "").strip().splitlines()
    summary = " | ".join([ln for ln in tail if "完成" in ln or "错误" in ln][-2:]) \
        or (tail[-1] if tail else "")
    return pdf.name, r.returncode, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("srcdir")
    ap.add_argument("--out", default="data")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--threads", type=int, default=1, help="每个子进程的推理线程数")
    ap.add_argument("--only", default=None, help="逗号分隔的期号关键字，只跑这些")
    ap.add_argument("--no-write", action="store_true",
                    help="只填 OCR 缓存不写 JSON（分片/二次解析模式）")
    args = ap.parse_args()

    src = Path(args.srcdir)
    pdfs = sorted(src.glob("*.pdf"))
    if args.only:
        keys = [k.strip() for k in args.only.split(",")]
        pdfs = [p for p in pdfs if any(k in p.name for k in keys)]
    if not pdfs:
        print("[提示] 没有匹配的 PDF")
        return 0

    print(f"并行 OCR {len(pdfs)} 期，workers={args.workers}，dpi={args.dpi}\n")
    results = []
    extra = ("--no-write",) if args.no_write else ()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, p, args.out, args.dpi, args.threads, extra): p
                for p in pdfs}
        for f in as_completed(futs):
            name, code, summary = f.result()
            flag = "OK " if code == 0 else f"ERR({code})"
            print(f"[{flag}] {name}  {summary}")
            results.append((name, code))
    bad = [n for n, c in results if c != 0]
    print(f"\n完成：成功 {len(results) - len(bad)} / {len(results)}")
    if bad:
        print("失败：", "; ".join(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
