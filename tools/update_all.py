# -*- coding: utf-8 -*-
"""
信息价一键更新
==============
扫描指定目录（默认 ../00_原始资料/信息价）下的所有 PDF，
把还没抽取过的自动抽取为 data/<期号>.json + .js，并刷新 data/index.js。

已抽取过的 PDF 会跳过（依据 data/_manifest.json 中记录的 文件名 + 大小）。

用法：
    python tools/update_all.py                     # 用默认目录
    python tools/update_all.py "D:/信息价PDF目录"   # 指定目录
    python tools/update_all.py --force             # 忽略缓存，全部重抽
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"

# 依次探测：优先本目录旁的原始资料目录，其次水利数字化底座项目路径
_DEFAULT_CANDIDATES = [
    ROOT.parent / "00_原始资料" / "信息价",
    Path("F:/SynologyDrive/工程文件/workbuddy工作区/水利数字化底座/00_原始资料/信息价"),
]


def default_src():
    for p in _DEFAULT_CANDIDATES:
        if p.exists():
            return p
    return _DEFAULT_CANDIDATES[-1]


def load_manifest():
    p = DATA / "_manifest.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_manifest(m):
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "_manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?", default=None, help="PDF 所在目录（默认自动探测）")
    ap.add_argument("--force", action="store_true", help="忽略缓存，全部重抽")
    args = ap.parse_args()

    src = Path(args.src) if args.src else default_src()
    if not src.exists():
        print(f"[错误] 目录不存在：{src}")
        return 2
    pdfs = sorted(src.glob("*.pdf"))
    if not pdfs:
        print(f"[提示] {src} 下没有 PDF 文件")
        return 0

    man = load_manifest()
    todo = []
    for pdf in pdfs:
        key = f"{pdf.name}|{pdf.stat().st_size}"
        if not args.force and man.get(key, {}).get("done"):
            print(f"[跳过] {pdf.name}（已抽取过，期号 {man[key].get('issue')}）")
            continue
        todo.append(pdf)

    if not todo:
        print("所有 PDF 都已抽取，无需更新。")
        return 0

    print(f"待抽取 {len(todo)} 个文件，共发现 {len(pdfs)} 个 PDF。\n")
    ok = 0
    for pdf in todo:
        print(f"==== 抽取 {pdf.name} ====")
        r = subprocess.run([sys.executable, str(HERE / "extract_info_price.py"),
                            str(pdf), "--out", str(DATA)])
        if r.returncode == 0:
            ok += 1
            man[f"{pdf.name}|{pdf.stat().st_size}"] = {"done": True, "file": pdf.name}
        else:
            print(f"[失败] {pdf.name} 抽取返回码 {r.returncode}")
        print()

    save_manifest(man)
    print(f"完成：成功 {ok} / {len(todo)}。")

    # 有新数据时重建趋势索引
    if ok:
        print("重建历史趋势索引…")
        r = subprocess.run([sys.executable, str(HERE / "build_trends.py")])
        if r.returncode != 0:
            print("[警告] 趋势索引重建失败，可手动运行 tools/build_trends.py")

    print("前端刷新页面即可看到新期数。")
    return 0 if ok == len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())
