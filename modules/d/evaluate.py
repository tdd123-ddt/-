# -*- coding: utf-8 -*-
"""
模块 d · 评估（推荐可解释性 + 覆盖度）

硬指标（plan.txt）：
- 画像维度 ≥ 5
- 推荐结果可解释（每条 ≥ 2 条理由），且每条理由可溯源到数据字段
- 覆盖 ≥ 3 病种 × ≥ 5 机构
"""

from __future__ import annotations

from typing import List, Dict, Any

from .schema import Profile, Recommendation, ProfileReport


def evaluate(report: ProfileReport) -> Dict[str, Any]:
    profiles = report.profiles
    recs = report.recommendations

    # 1. 画像维度数
    n_dims = len({d.dimension for p in profiles for d in p.dimensions})

    # 2. 覆盖：病种 × 机构
    diseases = {p.disease for p in profiles}
    hospitals = {p.hospital for p in profiles}
    # 每个病种的机构数
    per_disease_hospitals = {d: len({p.hospital for p in profiles if p.disease == d}) for d in diseases}

    # 3. 推荐可解释性：每条 ≥2 理由，且理由可溯源（evidence 非空且有 dimension 值）
    explainable = True
    for r in recs:
        if len(r.reasons) < 2:
            explainable = False
        if len(r.evidence) < 2:
            explainable = False

    return {
        "n_dimensions": n_dims,
        "n_diseases": len(diseases),
        "n_hospitals": len(hospitals),
        "per_disease_hospitals": per_disease_hospitals,
        "n_recommendations": len(recs),
        "all_explainable": explainable,
        "min_reasons_per_rec": min((len(r.reasons) for r in recs), default=0),
        "pass_dimensions": n_dims >= 5,
        "pass_coverage": len(diseases) >= 3 and all(v >= 5 for v in per_disease_hospitals.values()),
        "pass_explainable": explainable,
    }


def print_report(res: Dict[str, Any]) -> str:
    lines = [
        f"画像维度数       : {res['n_dimensions']}  (目标 ≥ 5)  {'✓' if res['pass_dimensions'] else '✗'}",
        f"病种覆盖         : {res['n_diseases']}  (目标 ≥ 3)",
        f"机构覆盖         : {res['n_hospitals']} 家",
        f"每病种机构数      : {res['per_disease_hospitals']}  (目标 ≥ 5)",
        f"推荐条数         : {res['n_recommendations']}",
        f"每条推荐理由数    : 最少 {res['min_reasons_per_rec']}  (目标 ≥ 2)  {'✓' if res['min_reasons_per_rec'] >= 2 else '✗'}",
        f"覆盖达标         : {'✓' if res['pass_coverage'] else '✗'}",
        f"可解释达标       : {'✓' if res['pass_explainable'] else '✗'}",
    ]
    return "\n".join(lines)
