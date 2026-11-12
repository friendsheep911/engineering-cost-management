# -*- coding: utf-8 -*-
"""CI 自动更新：检查青海住建厅官网新一期《青海工程造价管理信息》并入库。

在 GitHub Actions（ubuntu）上运行，不依赖本机路径：
  1. 抓官网 xwdt/tzgg 与 zwfw/zjxx 列表页，定位《青海工程造价管理信息》文章页
  2. 提取文章内 P*.pdf 附件，与 data/official_issues.json 已知期次比对
  3. 新期：下载到 _raw/ → 补 official_issues.json（期号权威对照）→ 抽取
     → fix_dual_tax（海西双表）→ extract_seedlings（苗木）→ build_trends（趋势）
  4. 安全阀：抽取行数不足上期 60%（疑似 OCR 转曲期，CI 环境无法 OCR）→
     回滚 official_issues.json 与新数据文件，退出码 2 保持仓库不变

退出码：0 = 无新期或入库成功；2 = 发现新期但处理失败（人工介入）
"""
import io
import json
import re
import subprocess
import sys
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = ROOT / "_raw"
HERE = Path(__file__).resolve().parent

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
LIST_PAGES = [
    "https://zjt.qinghai.gov.cn/xwdt/tzgg/",
    "https://zjt.qinghai.gov.cn/zwfw/zjxx/",
]
ARTICLE_RE = re.compile(
    r'href="(https?://zjt\.qinghai\.gov\.cn/(?:xwdt/tzgg|zwfw/zjxx)/20\d{4}/t20\d+_\d+\.html)"')
PDF_RE = re.compile(r'(?:href|src)="([^"]+?P\d+\.pdf)"')
DIRECT_PDF_RE = re.compile(
    r'<li[^>]*>.*?<a[^>]+href="([^"]+?P\d+\.pdf)"[^>]*>\s*(.*?)\s*</a>'
    r'.*?<span[^>]*class="rt"[^>]*>\s*\[(20\d{2}-\d{2}-\d{2})\]',
    re.DOTALL | re.IGNORECASE,
)
ISSUE_RE = re.compile(r"(20\d{2})\s*年\s*第\s*([0-9一二三四五六七八九十]+)\s*期")
DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}


def fetch(url, binary=False, timeout=90, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": "https://zjt.qinghai.gov.cn/",
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
            return b if binary else b.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  [重试{i+1}] {url} -> {e}")
    raise RuntimeError(f"抓取失败 {url}: {last}")


def absolutize(u):
    if u.startswith("http"):
        return u
    if u.startswith("/"):
        return "https://zjt.qinghai.gov.cn" + u
    return "https://zjt.qinghai.gov.cn/" + u


def find_new_articles():
    """返回 [(article_url, pdf_url, title, date, issue)]，期号不在已知集合中的新文章。"""
    off = json.loads((DATA / "official_issues.json").read_text(encoding="utf-8"))
    known = {x["issue"] for x in off["issues"]}
    out, seen = [], set()
    for lp in LIST_PAGES:
        try:
            html = fetch(lp)
        except Exception as e:  # noqa: BLE001
            print(f"[警告] 列表页抓取失败 {lp}: {e}")
            continue
        # 新版官网会把 PDF 直接挂在列表页，不再提供独立文章页。
        for pm in DIRECT_PDF_RE.finditer(html):
            pdf_url = urljoin_abs(lp, pm.group(1))
            title = re.sub(r"<[^>]+>", "", pm.group(2)).strip()
            if pdf_url in seen or "造价管理信息" not in title:
                continue
            seen.add(pdf_url)
            issue = parse_issue(title)
            if issue is None:
                print(f"  [跳过] 无法识别期号：{title} | {pdf_url}")
                continue
            if issue in known:
                print(f"  已入库（{issue}），跳过")
                continue
            out.append((lp, pdf_url, title, pm.group(3), issue))
        for m in ARTICLE_RE.finditer(html):
            aurl = m.group(1)
            if aurl in seen:
                continue
            seen.add(aurl)
            try:
                art = fetch(aurl)
            except Exception as e:  # noqa: BLE001
                print(f"  [警告] 文章抓取失败 {aurl}: {e}")
                continue
            if "造价管理信息" not in art:
                continue
            tm = re.search(r"<title>([^<]+)</title>", art)
            title = (tm.group(1).strip() if tm else "")
            issue = parse_issue(title) or parse_issue(art[:4000])
            if issue is None:
                print(f"  [跳过] 无法识别期号：{aurl}")
                continue
            if issue in known:
                print(f"  已入库（{issue}），跳过")
                continue
            pm = PDF_RE.search(art)
            if not pm:
                print(f"  [跳过] {issue} 文章页无 PDF 附件")
                continue
            pdf_url = urljoin_abs(aurl, pm.group(1))
            dm = DATE_RE.search(art)
            out.append((aurl, pdf_url, title, dm.group(0) if dm else "", issue))
    return out


def urljoin_abs(base, u):
    return urllib.parse.urljoin(base, u)


def parse_issue(title):
    m = ISSUE_RE.search(title)
    if not m:
        return None
    year, no = m.group(1), m.group(2)
    no = CN_NUM.get(no) or (int(no) if no.isdigit() else None)
    if no is None:
        return None
    return f"{year}年第{no}期"


def prev_rows_total(issue_key):
    """取当前期之前最新一期的总行数，做量级安全阀。"""
    current = re.fullmatch(r"(20\d{2})年第(\d+)期", issue_key)
    current_order = (int(current.group(1)), int(current.group(2))) if current else None
    candidates = []
    for f in DATA.glob("20*年第*期.json"):
        m = re.fullmatch(r"(20\d{2})年第(\d+)期\.json", f.name)
        if not m:
            continue
        order = (int(m.group(1)), int(m.group(2)))
        if current_order is None or order < current_order:
            candidates.append((order, f))
    if not candidates:
        return None
    _, best = max(candidates, key=lambda item: item[0])
    d = json.loads(best.read_text(encoding="utf-8"))
    return best.name, sum(len(s.get("rows", [])) for s in d.get("sections", []))


def run(cmd):
    print(f"$ {' '.join(cmd)}")
    r = subprocess.run(cmd)
    return r.returncode


def main():
    news = find_new_articles()
    if not news:
        print("官网无新一期，仓库已是最新。")
        return 0
    # 只处理一篇（双月刊；若一次发布多篇，剩余下轮处理，便于逐期核查）
    aurl, pdf_url, title, pdate, issue = news[0]
    print(f"\n发现新期：{issue} | {title or '(无标题)'} | {pdate} | {pdf_url}")

    off_path = DATA / "official_issues.json"
    off_orig = off_path.read_text(encoding="utf-8")
    new_json = DATA / f"{issue}.json"
    if new_json.exists():
        print(f"[失败] {issue} 的数据文件已存在但官网清单未登记，请人工核对。")
        return 2

    RAW.mkdir(exist_ok=True)
    pdf_path = RAW / f"青海工程造价管理信息_{issue}.pdf"
    print(f"下载 {pdf_url} -> {pdf_path}")
    try:
        b = fetch(pdf_url, binary=True, timeout=300, retries=1)
    except Exception as e:  # noqa: BLE001
        print(f"[失败] PDF 下载失败：{e}")
        return 2
    pdf_path.write_bytes(b)
    print(f"下载完成：{len(b)/1e6:.1f} MB")

    # 补官网清单（extract_info_price.py 靠它做期号权威对照）
    off = json.loads(off_orig)
    off["issues"].append({
        "issue": issue, "published": (pdate or "") + " 00:00",
        "page": aurl, "attach": pdf_url,
    })
    off["count"] = len(off["issues"])
    off_path.write_text(json.dumps(off, ensure_ascii=False, indent=1), encoding="utf-8")

    guard = prev_rows_total(issue)
    ok = True
    try:
        if run([sys.executable, str(HERE / "extract_info_price.py"),
                str(pdf_path), "--out", str(DATA)]) != 0 or not new_json.exists():
            print("[失败] 抽取失败")
            ok = False
        if ok:
            d = json.loads(new_json.read_text(encoding="utf-8"))
            rows = sum(len(s.get("rows", [])) for s in d["sections"])
            nsec = len(d["sections"])
            print(f"抽取结果：{nsec} sections / {rows} rows")
            if guard:
                _, base = guard
                print(f"上期基准：{base} rows")
                if rows < base * 0.6 or nsec < 15:
                    print("[失败] 行数骤降（疑似 OCR 转曲期，CI 无法 OCR）或表数异常，回滚。")
                    ok = False
        if ok:
            for tool in ("fix_dual_tax.py",):
                if run([sys.executable, str(HERE / tool)]) != 0:
                    print(f"[失败] {tool} 返回非零")
                    ok = False
            if ok and run([sys.executable, str(HERE / "extract_seedlings.py"),
                           str(pdf_path)]) != 0:
                print("[警告] 苗木抽取失败（可能本期无苗木附件），继续")
            if ok and run([sys.executable, str(HERE / "build_trends.py")]) != 0:
                print("[失败] 趋势重建失败")
                ok = False
            if ok and run([sys.executable, str(HERE / "export_issue_xlsx.py"), issue]) != 0:
                print("[失败] Excel 导出失败")
                ok = False
            if ok and run([sys.executable, str(HERE / "_final_check.py")]) != 0:
                print("[失败] 终检未通过")
                ok = False
    finally:
        if not ok:
            # 回滚本次改动，保持仓库与运行前一致
            off_path.write_text(off_orig, encoding="utf-8")
            for f in (new_json, DATA / f"{issue}.js"):
                if f.exists():
                    f.unlink()
            print("已回滚官网清单与本次数据文件。")
        else:
            print(f"\n入库成功：{issue}。由 workflow 提交推送。")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
