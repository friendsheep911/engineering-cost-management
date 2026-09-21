# -*- coding: utf-8 -*-
"""按官网期次清单批量下载《青海工程造价管理信息》PDF。

用法：
    python download_issues.py                 # 下载清单里全部缺失期次
    python download_issues.py --list          # 只打印清单，不下载
    python download_issues.py --only 2026     # 只下载指定年份

特性：
- 断点续跑：目标文件已存在且大小>1MB 则跳过
- 按大小去重：目录中已有同大小文件（如手工下载的原件）则跳过并登记
- 失败重试 3 次，带 Referer 伪装浏览器
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LIST_JSON = ROOT / "data" / "official_issues.json"
DEFAULT_DIR = ROOT.parent / "00_原始资料" / "信息价"

HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": "https://zjt.qinghai.gov.cn/"}


def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers=HDRS)
    return urllib.request.urlopen(req, timeout=timeout)


def download_one(url, dest: Path) -> str:
    """返回 'ok' | 'skip' | 'fail:原因'"""
    if dest.exists() and dest.stat().st_size > 1024 * 1024:
        return "skip"
    for attempt in range(3):
        try:
            with fetch(url) as r:
                total = int(r.headers.get("Content-Length") or 0)
                data = r.read()
            if len(data) < 100 * 1024:
                return f"fail:文件过小({len(data)}B)，疑似无效"
            dest.write_bytes(data)
            _ = total
            return "ok"
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                return f"fail:{e}"
            time.sleep(3 * (attempt + 1))
    return "fail:未知"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(DEFAULT_DIR), help="PDF 保存目录")
    ap.add_argument("--list", action="store_true", help="仅列出清单")
    ap.add_argument("--only", default="", help="仅下载指定年份，如 2024")
    args = ap.parse_args()

    if not LIST_JSON.exists():
        print(f"[错误] 清单不存在：{LIST_JSON}")
        return 2
    issues = json.loads(LIST_JSON.read_text(encoding="utf-8"))["issues"]
    if args.only:
        issues = [o for o in issues if o["issue"].startswith(args.only)]

    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    local_sizes = {f.stat().st_size: f.name for f in dest_dir.glob("*.pdf") if f.is_file()}

    if args.list:
        for o in issues:
            print(f"{o['issue']}  {o['published']}  {o['attach']}")
        return 0

    ok = skip = dup = 0
    fails = []
    for o in issues:
        name = f"青海工程造价管理信息_{o['issue']}.pdf"
        dest = dest_dir / name
        url = o["attach"]
        if not url.startswith("http"):
            fails.append((o["issue"], "清单中无有效附件链接"))
            print(f"[失败] {o['issue']} 无有效链接")
            continue
        # 按大小去重：远端大小与本地已有文件一致则跳过
        try:
            with fetch(url, timeout=30) as r:
                remote_size = int(r.headers.get("Content-Length") or 0)
        except Exception as e:  # noqa: BLE001
            fails.append((o["issue"], str(e)))
            print(f"[失败] {o['issue']} 获取大小失败: {e}")
            continue
        if remote_size in local_sizes:
            dup += 1
            print(f"[去重] {o['issue']} 与本地 {local_sizes[remote_size]} 相同，跳过")
            continue
        r = download_one(url, dest)
        if r == "ok":
            ok += 1
            print(f"[完成] {name}  {remote_size/1048576:.1f} MB")
        elif r == "skip":
            skip += 1
            print(f"[跳过] {name} 已存在")
        else:
            fails.append((o["issue"], r))
            print(f"[失败] {o['issue']} {r}")
        time.sleep(1)  # 温和抓取

    print(f"\n汇总：新下载 {ok}，已存在 {skip}，去重 {dup}，失败 {len(fails)}")
    for iss, why in fails:
        print(f"  - {iss}: {why}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
