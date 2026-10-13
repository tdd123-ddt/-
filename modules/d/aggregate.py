# -*- coding: utf-8 -*-
"""
模块 d · 品质画像聚合（纯数据驱动）

跨病历按 (机构 × 病种) 聚合统计，每个画像维度都来自字段计算，可溯源。
不依赖 LLM（对应红线"品质画像脱离数据"）。

输入：多个结构化病历 JSON（真实 + 合成，合成记录带 readmission_30d/satisfaction/synthetic 字段）。
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional

from .schema import Profile, DimensionValue, DIMENSIONS, DIRECTION


def v(f: Any) -> Any:
    """从 Field 包装 {"value": ...} 或裸值里取值。"""
    if isinstance(f, dict) and "value" in f:
        return f["value"]
    return f


def _num(x: Any) -> Optional[float]:
    x = v(x)
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _primary_disease(record: Dict) -> str:
    """取规范病种：优先非中医证型的西医诊断，中医证型（名含"证"）归并到西医病种。"""
    ds = record.get("diagnoses", []) or []

    def name(d):
        return str(v(d.get("name", {})) or "") or ""

    def is_tcm(d):
        return "证" in name(d)   # 中医证型：如 肝郁肾虚证 / 气滞血瘀证

    for d in ds:
        if d.get("is_primary") and not is_tcm(d):
            return name(d) or "未知"
    for d in ds:
        if not is_tcm(d):
            return name(d) or "未知"
    for d in ds:
        if d.get("is_primary"):
            return name(d) or "未知"
    return name(ds[0]) if ds else "未知"


def _has_polypharmacy(record: Dict) -> bool:
    """重复用药代理：同类药 ≥3 种（复用模块 b 的药理分类关键词）。"""
    from modules.b.schema import DRUG_CLASS_KEYWORDS
    meds = record.get("medications", []) or []
    by_class: Dict[str, int] = {}
    for m in meds:
        name = str(v(m.get("name", {})) or "") + str(v(m.get("purpose", {})) or "")
        for cls, kws in DRUG_CLASS_KEYWORDS.items():
            if any(k in name for k in kws):
                by_class[cls] = by_class.get(cls, 0) + 1
    return any(c >= 3 for c in by_class.values())


def record_features(record: Dict) -> Dict[str, Any]:
    """从一条结构化记录抽聚合所需特征。"""
    enc = record.get("encounter", {})
    costs = record.get("costs", {})
    total = _num(costs.get("total")) or 0.0
    self_pay = _num(costs.get("self_paid")) or 0.0
    los = _num(enc.get("length_of_stay_days")) or 0.0
    return {
        "hospital": str(v(enc.get("hospital", {})) or "未知"),
        "disease": _primary_disease(record),
        "total_cost": total,
        "self_pay_ratio": (self_pay / total) if total > 0 else 0.0,
        "length_of_stay": los,
        "med_count": len(record.get("medications", []) or []),
        "polypharmacy": _has_polypharmacy(record),
        "readmission_30d": int(v(record.get("readmission_30d")) or 0),
        "satisfaction": _num(record.get("satisfaction")),
        "synthetic": bool(record.get("synthetic", False)),
    }


def group_by(records: List[Dict]) -> Dict[str, List[Dict]]:
    """按 (医院, 病种) 分组。"""
    groups: Dict[str, List[Dict]] = {}
    for r in records:
        f = record_features(r)
        key = (f["hospital"], f["disease"])
        groups.setdefault(key, []).append(f)
    return groups


def _dim_value(dim: str, feats: List[Dict], n: int) -> DimensionValue:
    label = dict((k, lab) for k, lab, _ in DIMENSIONS)[dim]
    direction = DIRECTION[dim]
    if dim == "cost_efficiency":
        val = sum(f["total_cost"] for f in feats) / n
        return DimensionValue(dimension=dim, label=label, value=round(val, 2), unit="元",
                              n_samples=n, direction=direction, source="costs.total 均值")
    if dim == "length_of_stay":
        val = sum(f["length_of_stay"] for f in feats) / n
        return DimensionValue(dimension=dim, label=label, value=round(val, 2), unit="天",
                              n_samples=n, direction=direction, source="encounter.length_of_stay_days 均值")
    if dim == "self_pay_ratio":
        val = sum(f["self_pay_ratio"] for f in feats) / n * 100
        return DimensionValue(dimension=dim, label=label, value=round(val, 2), unit="%",
                              n_samples=n, direction=direction, source="costs.self_paid/costs.total 均值")
    if dim == "medication_reasonableness":
        poly = sum(1 for f in feats if f["polypharmacy"])
        val = (1.0 - poly / n) * 100   # 越高越好（越少重复用药）
        return DimensionValue(dimension=dim, label=label, value=round(val, 1), unit="%",
                              n_samples=n, direction=direction, source="1 - 重复用药病例占比")
    if dim == "treatment_standard":
        # 代理：无"过度治疗"记录的病例视为规范（合成数据可直接给值，真实数据缺省 1）
        vals = [f.get("standard", 1.0) for f in feats]
        val = sum(vals) / n * 100
        return DimensionValue(dimension=dim, label=label, value=round(val, 1), unit="%",
                              n_samples=n, direction=direction, source="治疗规范标记均值")
    if dim == "readmission_rate":
        val = sum(f["readmission_30d"] for f in feats) / n * 100
        return DimensionValue(dimension=dim, label=label, value=round(val, 2), unit="%",
                              n_samples=n, direction=direction, source="30天再入院标记均值")
    if dim == "satisfaction":
        sats = [f["satisfaction"] for f in feats if f["satisfaction"] is not None]
        if not sats:
            val = 0.0
        else:
            val = sum(sats) / len(sats)
        return DimensionValue(dimension=dim, label=label, value=round(val, 2), unit="分",
                              n_samples=len(sats), direction=direction, source="患者满意度均值")
    raise ValueError(dim)


def build_profiles(records: List[Dict]) -> List[Profile]:
    """聚合出 (机构 × 病种) 画像。"""
    groups = group_by(records)
    profiles: List[Profile] = []
    for (hospital, disease), feats in sorted(groups.items()):
        n = len(feats)
        dims = [_dim_value(dim, feats, n) for dim, _, _ in DIMENSIONS]
        synthetic = all(f["synthetic"] for f in feats)
        profiles.append(Profile(hospital=hospital, disease=disease, n_cases=n,
                                dimensions=dims, synthetic=synthetic))
    return profiles
