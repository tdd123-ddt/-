# -*- coding: utf-8 -*-
"""
模块 a · 字段级置信度计算

置信度 = 模型置信 × 规则交叉校验 × OCR 置信 的加权合成。
关键交叉校验（plan.txt 明确要求"住院天数=出院日-入院日"这类）：
1. 住院天数 vs (出院时间 - 入院时间)
2. 总费用 vs (医保支付 + 个人支付 + 补助减免)   —— 允许合理误差
3. 医保分类汇总之和 vs 总费用                    —— 有分类汇总时校验
4. 字段有 evidence 溯源 → 置信加分；无 evidence 且非缺省 → 降分

同时提供 missing-rate 计算（评估用）。
"""

from __future__ import annotations

from typing import Optional, List

from .schema import StructuredRecord, Field


def _val(f: Field):
    return f.value if isinstance(f, Field) else f


def _fval(f: Optional[Field]):
    if f is None:
        return None
    return f.value


def _num(f: Optional[Field]) -> Optional[float]:
    v = _fval(f)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def cross_check_length_of_stay(rec: StructuredRecord) -> float:
    """住院天数 vs 出院-入院 交叉校验，返回 0~1 的置信系数。"""
    los = _num(rec.encounter.length_of_stay_days)
    adm = _fval(rec.encounter.admission_time)
    dis = _fval(rec.encounter.discharge_time)
    if los is None:
        return 0.5  # 无住院天数，中性
    if not adm or not dis:
        return 0.5  # 无日期可校验
    from . import rules
    diff = rules.days_between(str(adm), str(dis))
    if diff is None:
        return 0.5
    if abs(int(los) - diff) <= 1:
        return 1.0
    return 0.3  # 冲突，降置信


def cross_check_cost(rec: StructuredRecord) -> float:
    """总费用 vs 各支付方之和 校验。"""
    total = _num(rec.costs.total)
    if total is None:
        return 0.5
    parts = [p for p in (_num(rec.costs.insurance_paid),
                         _num(rec.costs.self_paid),
                         _num(rec.costs.other_paid)) if p is not None]
    if not parts:
        # 无分项：看分类汇总
        s = sum(x.amount.value for x in rec.costs.insurance_class_summary if _num(x.amount) is not None)
        if s > 0 and abs(s - total) / total < 0.05:
            return 1.0
        return 0.6
    s = sum(parts)
    if abs(s - total) / total < 0.05:
        return 1.0
    return 0.5


def recompute_confidence(rec: StructuredRecord) -> StructuredRecord:
    """在抽取结果上叠加规则交叉校验，修正关键字段置信度。"""
    los_coef = cross_check_length_of_stay(rec)
    cost_coef = cross_check_cost(rec)

    # 住院天数
    f = rec.encounter.length_of_stay_days
    if f.value is not None:
        f.confidence = _clamp(f.confidence * los_coef)

    # 总费用
    if rec.costs.total.value is not None:
        rec.costs.total.confidence = _clamp(rec.costs.total.confidence * cost_coef)

    # 证据溯源加成：有 evidence 的字段微调（缺失且无 evidence 的保持低分）
    def _walk_fields(obj):
        if isinstance(obj, Field):
            if obj.value is not None and obj.evidence and obj.confidence < 0.95:
                obj.confidence = _clamp(obj.confidence + 0.03)
            if obj.value is None:
                obj.missing = True
            return
        if isinstance(obj, list):
            for it in obj:
                _walk_fields(it)
            return
        if hasattr(obj, "__dict__"):
            for v in vars(obj).values():
                _walk_fields(v)

    for field_name in ("diagnoses", "procedures", "medications", "labs_imaging", "timeline"):
        for item in getattr(rec, field_name):
            _walk_fields(item)
    for section in ("patient", "encounter", "costs", "discharge"):
        _walk_fields(getattr(rec, section))

    return rec


# ---------------------------------------------------------------------------
# 缺失率 / 字段清单（评估用）
# ---------------------------------------------------------------------------

# 关键字段清单（对齐 plan.txt 的"关键字段"定义：诊断/手术/用药/住院天数/费用构成）
# 每个字段带 applicability：是否随就诊类型适用（住院 vs 门诊）。
#   always     —— 任何病例都要求有
#   inpatient  —— 仅住院病例要求有（门诊视为"不适用"，不计入缺失率分母）
#   conditional—— 视内容而定（如：无手术的门诊病例，手术字段不适用）
KEY_FIELDS = [
    # (路径, 类型, 适用条件)
    ("diagnoses", "list", "always"),
    ("procedures", "list", "conditional"),          # 有手术才要求
    ("medications", "list", "always"),
    ("costs.total", "scalar", "always"),
    ("costs.insurance_paid", "scalar", "conditional"),  # 官方数据仅结算单病例提供
    ("costs.self_paid", "scalar", "conditional"),       # 官方数据仅结算单病例提供
    ("costs.breakdown", "list", "always"),          # 分类占比
    ("encounter.length_of_stay_days", "scalar", "inpatient"),
    ("encounter.admission_time", "scalar", "inpatient"),
    ("encounter.discharge_time", "scalar", "inpatient"),
]


def _get(rec: StructuredRecord, path: str):
    cur = rec
    for part in path.split("."):
        cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def _is_inpatient(rec: StructuredRecord) -> bool:
    t = rec.encounter.type.value or ""
    return "住院" in t or "入出院" in t


def key_field_status(rec: StructuredRecord) -> dict:
    """返回每个关键字段的 {applicable, present, value, confidence}。

    - applicable=False 表示该字段对该病例类型不适用（不计入缺失率）。
    - present=False 且 applicable=True 才是真正"缺失"。
    """
    inp = _is_inpatient(rec)
    out = {}
    for path, kind, cond in KEY_FIELDS:
        cur = _get(rec, path)
        applicable = True
        if cond == "inpatient":
            applicable = inp
        elif cond == "conditional":
            # 手术字段：住院或已有手术记录时适用
            applicable = inp or bool(cur)

        if kind == "list":
            present = bool(cur)
            if cur:
                conf = sum((getattr(i, "name", None) or Field()).confidence for i in cur) / len(cur)
            else:
                conf = 0.0
            out[path] = {"applicable": applicable, "present": present, "value": None,
                         "confidence": conf}
        else:  # scalar
            present = cur.value is not None if isinstance(cur, Field) else False
            out[path] = {"applicable": applicable, "present": present,
                         "value": getattr(cur, "value", None),
                         "confidence": getattr(cur, "confidence", 0.0) if isinstance(cur, Field) else 0.0}
    return out


def missing_rate(rec: StructuredRecord) -> float:
    """关键字段缺失率 = 缺失(且适用)的关键字段数 / 适用关键字段数。"""
    st = key_field_status(rec)
    applicable = [v for v in st.values() if v["applicable"]]
    if not applicable:
        return 0.0
    miss = sum(1 for v in applicable if not v["present"])
    return miss / len(applicable)
