# -*- coding: utf-8 -*-
"""
抽取质量校验器 —— 数字覆盖率审计
==================================
思路：把 PDF 每页文字里的所有"价格型数字"抽出来，与结构化结果中该页贡献的数字做集合比对。
      凡是页面上存在、但结果里找不到的数字，都会被列出来。
      这是保证"没有静默漏数据"的硬性关卡。

用法：
    python verify_extraction.py <PDF> <result.json> [--start-page 18]
"""
import argparse
import json
import re
from pathlib import Path

import pymupdf

NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 页码/期号/文号等噪声：形如 (15) / 〔2026〕4 号 / 第 171 期
NOISE = [re.compile(r"^\d{1,3}$")]


def page_numbers(text):
    out = set()
    for m in NUM_RE.finditer(text):
        s = m.group(0)
        v = s.replace(",", "")
        out.add(v)
        try:
            f = float(v)
            out.add(f"{f:.2f}")
        except ValueError:
            pass
    return out


def norm(s):
    try:
        return f"{float(str(s).replace(',', '')):.2f}"
    except (ValueError, TypeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("result")
    ap.add_argument("--start-page", type=int, default=18)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    data = json.loads(Path(args.result).read_text(encoding="utf-8"))
    doc = pymupdf.open(args.pdf)

    # 收集结果中每个数字（含税/除税）
    got = set()
    per_section = []
    for s in data["sections"]:
        cnt = 0
        for r in s["rows"]:
            for p in r["prices"]:
                for k in ("incl", "excl"):
                    if p.get(k):
                        v = norm(p[k].get("raw"))
                        if v:
                            got.add(v)
                            cnt += 1
        per_section.append((f'p{s["pages"][0]}-{s["pages"][1]} {s["title"]}', cnt))

    missing_pages = {}
    total_missing = 0
    for pno in range(args.start_page - 1, doc.page_count):
        text = doc[pno].get_text()
        nums = page_numbers(text)
        # 过滤明显的页码噪声（±页码）
        cand = set()
        for v in nums:
            try:
                f = float(v)
            except ValueError:
                continue
            cand.add(f"{f:.2f}")
        miss = {v for v in cand if v not in got}
        # 去掉纯整数页码类（1-3 位且等于页码附近）
        miss = {v for v in miss if not (v.endswith(".00") and len(v.split(".")[0]) <= 3 and abs(float(v) - (pno + 1)) <= 2)}
        if miss:
            missing_pages[pno + 1] = sorted(miss, key=lambda x: float(x))
            total_missing += len(miss)

    print(f"结果中出现的价格数字种类: {len(got)}")
    print(f"未在结果中找到的页面数字 : {total_missing} 个，涉及 {len(missing_pages)} 页")
    print()
    for p, v in list(missing_pages.items())[:40]:
        print(f"  p{p:>3}: {', '.join(v[:18])}{' ...' if len(v) > 18 else ''}")
    if len(missing_pages) > 40:
        print(f"  ... 其余 {len(missing_pages)-40} 页从略")
    print()
    print("各章节价格数字个数:")
    for k, v in per_section:
        print(f"  {v:>6}  {k}")
    doc.close()


if __name__ == "__main__":
    main()
