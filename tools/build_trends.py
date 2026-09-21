# -*- coding: utf-8 -*-
"""
趋势索引预计算
==============
把 data/ 下全部期次 JSON 归并为「材料 → 各期各地区含税价序列」的紧凑索引，
输出 data/trends.js（window.__TRENDS__），供前端"历史趋势"页使用。

只在抽取完成后运行一次（update_all.py 末尾会自动调用），前端按需加载，
浏览时与单期查询一样流畅。

用法：
    python tools/build_trends.py
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"

# 地区别名归一（前后刊写法不一致）
AREA_ALIAS = {
    "格尔木(唐古拉镇)": "格尔木",
    "格尔木（唐古拉镇）": "格尔木",
    "化隆(群科新区)": "化隆",
    "化隆（群科新区）": "化隆",
    "天骏": "天峻",
}

# 规范化后的合法地区（短名）。不在名单内的碎片直接丢弃（表头残留等）
KNOWN_AREAS = {
    "西宁", "海东", "乐都", "平安", "民和", "互助", "化隆", "循化",
    "海晏", "祁连", "门源", "刚察",
    "同仁", "尖扎", "泽库", "河南",
    "共和", "贵德", "贵南", "同德", "兴海",
    "玛沁", "班玛", "甘德", "达日", "久治", "玛多",
    "玉树", "称多", "囊谦", "杂多", "治多", "曲麻莱",
    "德令哈", "格尔木", "乌兰", "都兰", "天峻", "大柴旦行委", "柴旦", "冷湖", "茫崖",
    "海北", "海南", "黄南", "果洛", "海西", "西海镇", "未识别",
}


def norm_area(raw):
    a = (raw or "").strip().replace("地区", "").strip()
    a = AREA_ALIAS.get(a, a)
    # 县名主体 = 首个数字/括号之前的汉字串（剔除"乌兰349.51（茶"这类黏连）
    m = re.match(r"^([\u4e00-\u9fa5]{2,6})", a)
    base = m.group(1) if m else ""
    if base.endswith("县") or base.endswith("市"):
        base = base[:-1]
    if base in KNOWN_AREAS:
        return base
    return a if a in KNOWN_AREAS else None


def main():
    # 1) 收集期次文件，按发布日期排序
    issues = []
    for f in sorted(DATA.glob("*.json")):
        if f.name in ("index.json", "official_issues.json", "_manifest.json"):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[跳过] {f.name}：{e}")
            continue
        if not d.get("sections"):
            continue
        issues.append(d)
    issues.sort(key=lambda d: (d.get("meta", {}).get("publishDate") or "9999",
                               d.get("meta", {}).get("period") or ""))
    if not issues:
        print("[错误] data/ 下没有可用期次数据")
        return 1

    periods = [{"k": d.get("issueKey"), "p": d.get("meta", {}).get("period"),
                "d": d.get("meta", {}).get("publishDate") or "",
                "n": d.get("meta", {}).get("issueNo")}
               for d in issues]
    pidx = {p["k"]: i for i, p in enumerate(periods)}

    # 2) 归并材料序列
    mats = []                      # [kind, name, spec, unit]
    mat_ids = {}                   # key -> mid
    areas = []                     # 规范化地区名
    area_ids = {}
    series = {}                    # mid -> {areaIdx: [[pIdx, num], ...]}

    def mid_of(kind, name, spec, unit):
        key = (kind, name, spec, unit)
        if key not in mat_ids:
            mat_ids[key] = len(mats)
            mats.append([kind, name, spec, unit])
            series[mat_ids[key]] = {}
        return mat_ids[key]

    def aid_of(a):
        if a not in area_ids:
            area_ids[a] = len(areas)
            areas.append(a)
        return area_ids[a]

    for pi, d in enumerate(issues):
        for s in d["sections"]:
            kind = s.get("kind", "")
            for r in s.get("rows", []):
                for p in r.get("prices", []):
                    num = (p.get("incl") or {}).get("num")
                    if not isinstance(num, (int, float)) or not p.get("area"):
                        continue
                    area = norm_area(p["area"])
                    if area is None:
                        continue
                    mid = mid_of(kind, r.get("name", ""), r.get("spec", ""), r.get("unit", ""))
                    aid = aid_of(area)
                    slot = series[mid].setdefault(aid, [])
                    # 同期同地区同材料若重复（不同 section），保留先出现的
                    if not slot or slot[-1][0] != pi:
                        slot.append([pi, round(num, 2)])

    # 3) 序列内按期排序 + 统计
    multi = 0
    for mid in series:
        for aid in series[mid]:
            series[mid][aid].sort(key=lambda x: x[0])
    trend_mats = sum(1 for mid in series
                     if len({x[0] for a in series[mid].values() for x in a}) >= 2)
    cells = sum(len(v) for m in series.values() for v in m.values())

    # 4) 写出 trends.js（紧凑格式：数组套数组省体积）
    out = {"periods": periods, "areas": areas, "mats": mats, "series": series}
    js = "window.__TRENDS__ = " + json.dumps(out, ensure_ascii=False,
                                            separators=(",", ":"), sort_keys=False) + ";"
    (DATA / "trends.js").write_text(js, encoding="utf-8")
    size_mb = (DATA / "trends.js").stat().st_size / 1048576

    print(f"趋势索引生成完成：data/trends.js（{size_mb:.1f} MB）")
    print(f"  期次 {len(periods)} 期 | 材料 {len(mats)} 种 | 地区 {len(areas)} 个")
    print(f"  价格点 {cells:,} 个 | 跨 2 期以上可画趋势的材料 {trend_mats} 种")
    return 0


if __name__ == "__main__":
    sys.exit(main())
