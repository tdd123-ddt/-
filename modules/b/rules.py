# -*- coding: utf-8 -*-
"""
模块 b · 规则引擎（确定性痛点打底）

对应 plan.txt：规则引擎打底（如"自费占比>阈值"→费用异常），LLM 语义判断补足。
这里的规则产出"费用/体验/重复用药"等确定性痛点，临床类语义痛点交给 detect.py 的 LLM。

输入是模块 a 的结构化 JSON dict（Field 包装：{"value": ..., "confidence": ...}）。
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional

from .schema import TAXONOMY, THRESHOLDS, JARGON_TERMS, DRUG_CLASS_KEYWORDS, PainPoint, Evidence


# ---------------------------------------------------------------------------
# 从 Field 包装里取值
# ---------------------------------------------------------------------------

def val(f: Any) -> Any:
    """从 Field 包装 {"value": ...} 或裸值里取值。"""
    if isinstance(f, dict) and "value" in f:
        return f["value"]
    return f


def num(f: Any) -> Optional[float]:
    v = val(f)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def txt(f: Any) -> str:
    v = val(f)
    return str(v) if v is not None else ""


def _pp(ptype: str, severity: str, score: float, reason: str,
        evidence: List[Evidence], method: str = "rule") -> PainPoint:
    t = TAXONOMY[ptype]
    return PainPoint(type=ptype, name=t["name"], category=t["category"],
                     severity=severity, score=round(score, 3), reason=reason,
                     evidence=evidence, method=method)


def _sev(ratio: float, bands: Dict[str, float]) -> Optional[str]:
    """按阈值带给出严重度。"""
    if ratio >= bands["高"]:
        return "高"
    if ratio >= bands["中"]:
        return "中"
    if ratio >= bands["低"]:
        return "低"
    return None


# ---------------------------------------------------------------------------
# 费用类
# ---------------------------------------------------------------------------

def _self_pay_amount(costs: Dict) -> Optional[float]:
    """自费金额：优先 self_paid；若为 0/空，用医保分类里的 丙类/自费 之和兜底。"""
    sp = num(costs.get("self_paid"))
    if sp is not None and sp > 0:
        return sp
    # 兜底：丙类/自费 类汇总
    s = 0.0
    for item in costs.get("insurance_class_summary", []) or []:
        cls = txt(item.get("category", {}))
        if cls in ("丙类", "自费", "自付"):
            amt = num(item.get("amount"))
            if amt:
                s += amt
    return s if s > 0 else (sp if sp is not None else None)


def cost_self_pay_high(record: Dict) -> Optional[PainPoint]:
    """自费比例异常（总费用过低的小额自费不视为异常）。"""
    costs = record.get("costs", {})
    total = num(costs.get("total"))
    self_pay = _self_pay_amount(costs)
    if not total or self_pay is None:
        return None
    if total < THRESHOLDS["min_total_for_cost"]:
        return None
    ratio = self_pay / total
    sev = _sev(ratio, THRESHOLDS["self_pay"])
    if sev is None:
        return None
    return _pp("cost_self_pay_high", sev, ratio,
               f"自费 {self_pay:.2f} 元 / 总费用 {total:.2f} 元 = {ratio:.1%}",
               [Evidence(source="costs.self_paid", snippet=f"自费 {self_pay:.2f} / 总 {total:.2f}")])


def cost_drug_ratio_high(record: Dict) -> Optional[PainPoint]:
    """药品费占比过高（西药费+中成药+中草药+中药饮片费 / 总费用）。"""
    costs = record.get("costs", {})
    total = num(costs.get("total"))
    if not total or total < THRESHOLDS["min_total_for_cost"]:
        return None
    drug_kw = ("药", "中成药", "中草药", "中药")
    drug_sum = 0.0
    for item in costs.get("breakdown", []) or []:
        cat = txt(item.get("category", {}))
        if any(k in cat for k in drug_kw):
            amt = num(item.get("amount"))
            if amt:
                drug_sum += amt
    if drug_sum <= 0:
        return None
    ratio = drug_sum / total
    sev = _sev(ratio, THRESHOLDS["drug_ratio"])
    if sev is None:
        return None
    return _pp("cost_drug_ratio_high", sev, ratio,
               f"药品费 {drug_sum:.2f} 元 / 总费用 {total:.2f} 元 = {ratio:.1%}",
               [Evidence(source="costs.breakdown", snippet=f"药品费 {drug_sum:.2f} / 总 {total:.2f}")])


# ---------------------------------------------------------------------------
# 临床类（规则可判定的部分）
# ---------------------------------------------------------------------------

def clinical_polypharmacy(record: Dict) -> List[PainPoint]:
    """重复用药/用药种类过多：按药理类别分组，同类 ≥N 种则提示。"""
    meds = record.get("medications", []) or []
    by_class: Dict[str, List[str]] = {}
    for m in meds:
        name = txt(m.get("name", {}))
        purpose = txt(m.get("purpose", {}))
        hay = name + purpose
        for cls, kws in DRUG_CLASS_KEYWORDS.items():
            if any(kw in hay for kw in kws):
                by_class.setdefault(cls, []).append(name)
    out = []
    for cls, names in by_class.items():
        if len(names) >= THRESHOLDS["polypharmacy_count"]:
            sev = "高" if len(names) >= 5 else "中"
            out.append(_pp(
                "clinical_polypharmacy", sev, min(1.0, len(names) / 5),
                f"同类（{cls}）用药 {len(names)} 种：{('、'.join(names))[:60]}",
                [Evidence(source="medications", snippet=f"{cls}类 {len(names)} 种")]))
    return out


def clinical_over_examination_trigger(record: Dict) -> Optional[PainPoint]:
    """过度检查的规则触发：检验/检查费绝对值或占比高时给出"疑似"，LLM 再确认严重度。"""
    labs = record.get("labs_imaging", []) or []
    count = len(labs)
    costs = record.get("costs", {})
    lab_fee = 0.0
    for item in costs.get("breakdown", []) or []:
        if any(k in txt(item.get("category", {})) for k in ("检验", "检查", "化验")):
            lab_fee += num(item.get("amount")) or 0
    total = num(costs.get("total")) or 0
    fee_ratio = (lab_fee / total) if total else 0
    # 触发信号：检查检验费 ≥500 元，或占比 ≥20%，或明细条目 ≥20
    triggered = (lab_fee >= 500) or (fee_ratio >= 0.20) or (count >= THRESHOLDS["over_exam_count"])
    if not triggered:
        return None
    return _pp("clinical_over_examination", "低", min(1.0, fee_ratio),
               f"检查检验费用 {lab_fee:.2f} 元（占总费用 {fee_ratio:.1%}），需结合诊断判断是否过度",
               [Evidence(source="costs.breakdown", snippet=f"检验/检查费 {lab_fee:.2f}，占比 {fee_ratio:.1%}")],
               method="rule")


# ---------------------------------------------------------------------------
# 体验类
# ---------------------------------------------------------------------------

def exp_jargon(record: Dict) -> List[PainPoint]:
    """诊断/医嘱术语晦涩：诊断里出现专业术语则提示。"""
    diagnoses = record.get("diagnoses", []) or []
    hits = []
    for d in diagnoses:
        name = txt(d.get("name", {}))
        matched = [t for t in JARGON_TERMS if t in name]
        if matched:
            hits.append((name, matched))
    out = []
    if hits:
        names = "、".join(n for n, _ in hits)
        terms = "、".join(sorted({t for _, ms in hits for t in ms}))
        out.append(_pp(
            "exp_jargon", "中", 0.7,
            f"诊断「{names}」含专业术语（{terms}），对患者可能晦涩，建议通俗解释",
            [Evidence(source="diagnoses", snippet=names)]))
    return out


def exp_lack_guidance(record: Dict) -> Optional[PainPoint]:
    """病历缺乏康复/健康指导。住院看出院医嘱，门诊看病历是否有指导。"""
    enc_type = txt(record.get("encounter", {}).get("type", {}))
    discharge = record.get("discharge", {}) or {}
    instructions = txt(discharge.get("instructions", {}))
    follow_up = txt(discharge.get("follow_up", {}))

    if "门诊" in enc_type:
        # 门诊：没有"出院医嘱"，看是否有健康指导（现病史/处理意见在 extra 或 discharge）
        guidance = instructions or follow_up
        if len(guidance.strip()) < THRESHOLDS["guidance_min_len"]:
            return _pp("exp_lack_guidance", "中", 0.7,
                       "门诊病历仅开具检查/用药，缺少生活、饮食、运动等健康指导",
                       [Evidence(source="discharge.instructions", snippet=guidance or "(空)")])
        return None
    # 住院：出院医嘱过短即视为缺乏康复指导
    if len(instructions.strip()) < THRESHOLDS["guidance_min_len"]:
        return _pp("exp_lack_guidance", "中", 0.7,
                   "出院小结缺少可执行的康复指导",
                   [Evidence(source="discharge.instructions", snippet=instructions or "(空)")])
    return None


def exp_lack_followup(record: Dict) -> Optional[PainPoint]:
    """随访/复诊安排缺失。"""
    discharge = record.get("discharge", {}) or {}
    follow_up = txt(discharge.get("follow_up", {}))
    instructions = txt(discharge.get("instructions", {}))
    if not follow_up.strip() and "复查" not in instructions and "复诊" not in instructions:
        return _pp("exp_lack_followup", "中", 0.7,
                   "未写明复诊时间/随访安排",
                   [Evidence(source="discharge.follow_up", snippet="(空)")])
    return None


# ---------------------------------------------------------------------------
# 理赔体验类（2026-10 扩展：对应命题"临床/费用/体验"里的"体验"，聚焦理赔流程体验）
# ---------------------------------------------------------------------------

def claim_pay_advance_need(record: Dict) -> Optional[PainPoint]:
    """垫付需求：大额自费或高总费用，患者可能有资金压力。"""
    costs = record.get("costs", {})
    total = num(costs.get("total")) or 0
    self_pay = _self_pay_amount(costs)
    if total < THRESHOLDS["min_total_for_cost"]:
        return None
    ratio = (self_pay / total) if (self_pay and total) else 0
    # 触发：自费 ≥3000 元，或自费占比 ≥40%
    if (self_pay and self_pay >= 3000) or ratio >= 0.40:
        sev = "高" if ratio >= 0.60 else "中"
        return _pp("claim_pay_advance_need", sev, min(1.0, ratio),
                   f"自费 {self_pay:.0f} 元（占比 {ratio:.0%}），可能存在垫付/先行赔付需求",
                   [Evidence(source="costs.self_paid", snippet=f"自费 {self_pay:.0f}，占比 {ratio:.0%}")])
    return None


def claim_deductible_opaque(record: Dict) -> Optional[PainPoint]:
    """免赔额/报销口径不透明：自费比例高时，报销预期不清晰。"""
    costs = record.get("costs", {})
    total = num(costs.get("total")) or 0
    self_pay = _self_pay_amount(costs)
    if total < THRESHOLDS["min_total_for_cost"] or not self_pay:
        return None
    ratio = self_pay / total
    if ratio >= 0.30:
        return _pp("claim_deductible_opaque", "中", 0.6,
                   f"自费占比 {ratio:.0%}，患者对'能报多少/免赔额'预期可能不清晰，建议主动告知报销口径",
                   [Evidence(source="costs.self_paid", snippet=f"自费占比 {ratio:.0%}")])
    return None


def claim_material_burden(record: Dict) -> Optional[PainPoint]:
    """材料补件负担：住院患者需保留大量理赔材料。"""
    enc_type = txt(record.get("encounter", {}).get("type", {}))
    los = num(record.get("encounter", {}).get("length_of_stay_days", {})) or 0
    if "住院" in enc_type or los >= 1:
        return _pp("claim_material_burden", "低", 0.5,
                   "住院患者理赔需保留诊断证明/费用清单/发票等材料，缺件会延误理赔，建议提前告知清单",
                   [Evidence(source="encounter.type", snippet=f"住院 {los:.0f} 天")])
    return None


def claim_progress_anxiety(record: Dict) -> Optional[PainPoint]:
    """理赔进度焦虑：高金额案件患者对进度/到账时间敏感。"""
    costs = record.get("costs", {})
    total = num(costs.get("total")) or 0
    if total >= 20000:
        return _pp("claim_progress_anxiety", "中", 0.6,
                   f"总费用 {total:.0f} 元（高金额），患者对理赔进度/到账时间更敏感，建议主动同步进度",
                   [Evidence(source="costs.total", snippet=f"总费用 {total:.0f}")])
    return None


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------

def detect_rules(record: Dict) -> List[PainPoint]:
    """规则引擎全部规则。"""
    out: List[PainPoint] = []
    p = cost_self_pay_high(record)
    if p:
        out.append(p)
    p = cost_drug_ratio_high(record)
    if p:
        out.append(p)
    out.extend(clinical_polypharmacy(record))
    p = clinical_over_examination_trigger(record)
    if p:
        out.append(p)
    out.extend(exp_jargon(record))
    p = exp_lack_guidance(record)
    if p:
        out.append(p)
    p = exp_lack_followup(record)
    if p:
        out.append(p)
    # 理赔体验类
    p = claim_pay_advance_need(record)
    if p:
        out.append(p)
    p = claim_deductible_opaque(record)
    if p:
        out.append(p)
    p = claim_material_burden(record)
    if p:
        out.append(p)
    p = claim_progress_anxiety(record)
    if p:
        out.append(p)
    return out
