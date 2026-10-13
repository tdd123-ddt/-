# -*- coding: utf-8 -*-
"""
模块 d · 品质画像与推荐 端到端入口

用法：
    python -m modules.d.run                 # 用 output/ 的真实病例 + 合成演示数据
    python -m modules.d.run --top-k 3
"""

from __future__ import annotations

import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from . import aggregate, recommend, demo_data
from .schema import ProfileReport


def run(output_dir: str = None, top_k: int = 3, n_per_hospital: int = 10) -> ProfileReport:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out_dir = output_dir or os.path.join(root, "output")

    print("[1/3] 加载真实病例 + 合成演示数据 ...")
    records = demo_data.all_records(out_dir, n_per_hospital=n_per_hospital)
    n_real = sum(1 for r in records if not r.get("synthetic"))
    print(f"      共 {len(records)} 条记录（真实 {n_real} 条 + 合成 {len(records) - n_real} 条）")

    print("[2/3] 聚合品质画像（机构 × 病种）...")
    profiles = aggregate.build_profiles(records)
    print(f"      生成 {len(profiles)} 个画像（机构×病种），覆盖 {len({p.disease for p in profiles})} 病种 × {len({p.hospital for p in profiles})} 机构")

    print("[3/3] 可解释推荐 ...")
    recs = recommend.recommend_all(profiles, top_k=top_k)

    report = ProfileReport(profiles=profiles, recommendations=recs)
    with open(os.path.join(out_dir, "quality_profiles.json"), "w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=2))

    # 可读版
    md = _render_md(report)
    with open(os.path.join(out_dir, "quality_profiles.md"), "w", encoding="utf-8") as f:
        f.write(md)

    print(f"\n推荐结果（每种病种 top{top_k}）：")
    for r in recs:
        print(f"  [{r.disease}] #{r.rank} {r.hospital}  (score={r.score})")
        for reason in r.reasons:
            print(f"      · {reason}")
    print(f"\n输出：{os.path.join(out_dir, 'quality_profiles.json')} 和 .md")
    return report


def _render_md(report: ProfileReport) -> str:
    lines = ["# 医院/机构品质画像与可解释推荐\n"]
    diseases = sorted({p.disease for p in report.profiles})
    for d in diseases:
        lines.append(f"\n## 病种：{d}\n")
        lines.append("| 机构 | 样本 | 均次费用(元) | 住院天数 | 自费比例 | 用药合理率 | 治疗规范率 | 再入院率 | 满意度 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for p in [p for p in report.profiles if p.disease == d]:
            m = {x.dimension: x for x in p.dimensions}
            tag = "（真实+合成）" if not p.synthetic else ""
            lines.append(
                f"| {p.hospital}{tag} | {p.n_cases} | {m['cost_efficiency'].value:.0f} "
                f"| {m['length_of_stay'].value:.1f} | {m['self_pay_ratio'].value:.0%} "
                f"| {m['medication_reasonableness'].value:.0%} | {m['treatment_standard'].value:.0%} "
                f"| {m['readmission_rate'].value:.0%} | {m['satisfaction'].value:.1f} |")
        lines.append("\n**推荐：**")
        for r in [r for r in report.recommendations if r.disease == d]:
            lines.append(f"- **#{r.rank} {r.hospital}**（综合分 {r.score}）")
            for reason in r.reasons:
                lines.append(f"  - {reason}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="模块 d 品质画像与推荐")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--n-per-hospital", type=int, default=10)
    args = ap.parse_args()
    run(top_k=args.top_k, n_per_hospital=args.n_per_hospital)


if __name__ == "__main__":
    main()
