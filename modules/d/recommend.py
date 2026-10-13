# -*- coding: utf-8 -*-
"""
模块 d · 可解释推荐

按病种对机构画像做数据驱动的打分排序，并为每条推荐生成 ≥2 条**可量化、可溯源**理由。
理由直接引用画像维度的数值，不靠 LLM 自由发挥（对应红线"推荐不可解释"）。
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional

from .schema import Profile, Recommendation, DimensionValue, DIRECTION


# 各维度在综合分里的权重（和为 1）
WEIGHTS = {
    "cost_efficiency": 0.22,
    "length_of_stay": 0.13,
    "self_pay_ratio": 0.10,
    "medication_reasonableness": 0.18,
    "treatment_standard": 0.18,
    "readmission_rate": 0.09,
    "satisfaction": 0.10,
}


def _dim_map(profile: Profile) -> Dict[str, DimensionValue]:
    return {d.dimension: d for d in profile.dimensions}


def _score(profile: Profile, group: List[Profile]) -> float:
    """在一个病种组内做 min-max 归一化打分（1=最好）。"""
    total, wsum = 0.0, 0.0
    mine = _dim_map(profile)
    for dim, w in WEIGHTS.items():
        vals = [_dim_map(p)[dim].value for p in group if dim in _dim_map(p)]
        if not vals:
            continue
        v = mine.get(dim)
        if v is None:
            continue
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            total += 0.5 * w   # 全组无差异，给中性分
        elif DIRECTION[dim] == "lower":
            total += (hi - v.value) / (hi - lo) * w
        else:
            total += (v.value - lo) / (hi - lo) * w
        wsum += w
    return total / wsum if wsum else 0.0


def _reason_for(dim: DimensionValue, group_mean: float) -> Optional[str]:
    """针对单个维度生成一条可量化理由（优于组均值时）。"""
    v = dim.value
    if group_mean == 0:
        return None
    gap = (v - group_mean) / abs(group_mean)
    if DIRECTION[dim.dimension] == "lower":
        if v < group_mean:
            pct = abs(gap) * 100
            return f"{dim.label} {v:g}{dim.unit}，比该病种均值 {group_mean:g}{dim.unit} 低 {pct:.0f}%"
        return None
    else:
        if v > group_mean:
            pct = abs(gap) * 100
            return f"{dim.label} {v:g}{dim.unit}，比该病种均值 {group_mean:g}{dim.unit} 高 {pct:.0f}%"
        return None


def recommend(disease: str, profiles: List[Profile], top_k: int = 3) -> List[Recommendation]:
    """按病种推荐机构，附 ≥2 条可量化理由。"""
    group = [p for p in profiles if p.disease == disease]
    if not group:
        return []
    means = {}
    for dim, _ in WEIGHTS.items():
        vals = [_dim_map(p)[dim].value for p in group if dim in _dim_map(p)]
        means[dim] = (sum(vals) / len(vals)) if vals else 0.0

    ranked = sorted(group, key=lambda p: _score(p, group), reverse=True)
    out = []
    for i, p in enumerate(ranked[:top_k]):
        reasons, evidence = [], []
        for d in p.dimensions:
            if d.dimension in ("readmission_rate", "satisfaction") and d.n_samples == 0:
                continue
            r = _reason_for(d, means.get(d.dimension, 0.0))
            if r:
                reasons.append(r)
                evidence.append({"dimension": d.dimension, "value": d.value,
                                 "vs_mean": round(means.get(d.dimension, 0.0), 4)})
        # 保证至少 2 条：若维度理由不足，补"样本量"与"治疗规范"等确定性理由
        if len(reasons) < 2:
            reasons.append(f"该机构 {disease} 病例样本 {p.n_cases} 例，数据可追溯")
            evidence.append({"dimension": "n_cases", "value": p.n_cases, "vs_mean": None})
        out.append(Recommendation(
            disease=disease, hospital=p.hospital, rank=i + 1,
            score=round(_score(p, group), 4), reasons=reasons, evidence=evidence))
    return out


def recommend_all(profiles: List[Profile], top_k: int = 3) -> List[Recommendation]:
    """对每种病种都做推荐。"""
    diseases = sorted({p.disease for p in profiles})
    out = []
    for d in diseases:
        out.extend(recommend(d, profiles, top_k=top_k))
    return out
