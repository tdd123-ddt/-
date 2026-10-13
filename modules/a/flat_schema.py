# -*- coding: utf-8 -*-
"""
模块 a · LLM 抽取用"扁平 schema" + 转回 StructuredRecord 的适配层

为什么需要这一层：
- 内部 schema（schema.py）用 Field{value,confidence,evidence} 包装 + 嵌套 $defs，
  对 DeepSeek 这类后端的 tool-calling 太复杂（容易返回空对象）。
- 抽取时让 LLM 填一个**扁平、具体、可空**的结构（flat_*），再由 coerce_to_record()
  包装成带字段级置信度/证据的 StructuredRecord。

字段名与 StructuredRecord 叶子字段一一对应，保证转换无歧义。
"""

from __future__ import annotations

import re
from typing import List, Optional, Any
from pydantic import BaseModel, Field as PField, field_validator

from .schema import (
    StructuredRecord, Field, Evidence, Patient, Encounter, Diagnosis, Procedure,
    Medication, Cost, CostItem, LabFinding, Discharge, TimelineEvent, DocumentInfo,
)


# ---------------------------------------------------------------------------
# 扁平模型（LLM 直接填这个）
# ---------------------------------------------------------------------------

class FlatPatient(BaseModel):
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[Any] = None           # 允许 "26岁" / 26 / "26"，统一转数字
    birth_date: Optional[str] = None
    patient_id: Optional[Any] = None    # 允许数字/字符串（病案号可能被当数字）
    phone: Optional[Any] = None
    id_number: Optional[Any] = None


class FlatEncounter(BaseModel):
    type: Optional[str] = None
    hospital: Optional[str] = None
    department: Optional[str] = None
    doctor: Optional[str] = None
    admission_time: Optional[str] = None
    discharge_time: Optional[str] = None
    length_of_stay_days: Optional[Any] = None   # 允许 "3天" / 3 / "3"
    visit_time: Optional[str] = None
    insurance_type: Optional[str] = None


class FlatDiagnosis(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    icd_code: Optional[str] = None
    is_primary: bool = False


class FlatProcedure(BaseModel):
    name: Optional[str] = None
    date: Optional[str] = None
    anesthesia: Optional[str] = None
    code: Optional[str] = None
    body_part: Optional[str] = None


class FlatMedication(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    spec: Optional[Any] = None          # 允许数字/字符串（如 6 或 "6g"），转 str
    dose: Optional[Any] = None
    frequency: Optional[Any] = None
    route: Optional[Any] = None
    course: Optional[Any] = None
    purpose: Optional[Any] = None


class FlatCostItem(BaseModel):
    category: Optional[str] = None
    amount: Optional[Any] = None          # 允许 "1234.5元" / 1234.5
    insurance_class: Optional[str] = None
    name: Optional[str] = None


class FlatCost(BaseModel):
    total: Optional[Any] = None           # 允许 "31233.19元" / 31233.19
    insurance_paid: Optional[Any] = None
    self_paid: Optional[Any] = None
    other_paid: Optional[Any] = None
    currency: Optional[str] = "CNY"
    breakdown: List[FlatCostItem] = PField(default_factory=list)
    insurance_class_summary: List[FlatCostItem] = PField(default_factory=list)


class FlatLab(BaseModel):
    name: Optional[str] = None
    result: Optional[str] = None
    date: Optional[str] = None


class FlatDischarge(BaseModel):
    instructions: Optional[str] = None
    follow_up: Optional[str] = None
    discharge_medications: List[FlatMedication] = PField(default_factory=list)

    @field_validator("discharge_medications", mode="before")
    @classmethod
    def _coerce_med_strs(cls, v):
        """容错：LLM 偶尔把出院带药输出成字符串数组（如 ["药名A","药名B"]），
        自动转成 FlatMedication(name=字符串)，避免整个 case 因校验失败被丢弃。"""
        if isinstance(v, list):
            out = []
            for it in v:
                if isinstance(it, str):
                    out.append({"name": it})
                else:
                    out.append(it)
            return out
        return v


class FlatTimelineEvent(BaseModel):
    date: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None


class FlatRecord(BaseModel):
    """LLM 抽取的扁平结果（所有叶子可空）。"""
    patient: FlatPatient = PField(default_factory=FlatPatient)
    encounter: FlatEncounter = PField(default_factory=FlatEncounter)
    diagnoses: List[FlatDiagnosis] = PField(default_factory=list)
    procedures: List[FlatProcedure] = PField(default_factory=list)
    medications: List[FlatMedication] = PField(default_factory=list)
    costs: FlatCost = PField(default_factory=FlatCost)
    labs_imaging: List[FlatLab] = PField(default_factory=list)
    discharge: FlatDischarge = PField(default_factory=FlatDischarge)
    timeline: List[FlatTimelineEvent] = PField(default_factory=list)
    extra: Optional[Any] = None           # 兜底字段，允许任意结构


# ---------------------------------------------------------------------------
# 包装辅助
# ---------------------------------------------------------------------------

def _wrap(value, conf=0.9, method="llm") -> Field:
    if value is None or value == "":
        return Field(value=None, confidence=0.0, missing=True, method=method)
    return Field(value=value, confidence=conf, missing=False, method=method)


def _str(value) -> Optional[str]:
    """把 LLM 可能返回的数字/字符串统一成字符串（None 保持 None）。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value)


def _num(value) -> Optional[float]:
    """把 LLM 可能返回的"26岁"/"3天"/"31233.19元"/26/3.0 清洗成数字。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def _med(m) -> Medication:
    return Medication(
        name=_wrap(m.name), category=_wrap(m.category), spec=_wrap(_str(m.spec), 0.85),
        dose=_wrap(_str(m.dose), 0.8), frequency=_wrap(_str(m.frequency), 0.85),
        route=_wrap(_str(m.route), 0.85), course=_wrap(_str(m.course), 0.85),
        purpose=_wrap(_str(m.purpose), 0.75),
    )


def coerce_to_record(flat: dict, patient_id: str = "") -> StructuredRecord:
    """把 LLM 扁平结果包装成 StructuredRecord。"""
    f = FlatRecord.model_validate(flat)
    rec = StructuredRecord(patient_id=patient_id)

    rec.patient = Patient(
        name=_wrap(f.patient.name), gender=_wrap(f.patient.gender),
        age=_wrap(_num(f.patient.age)), birth_date=_wrap(f.patient.birth_date),
        patient_id=_wrap(_str(f.patient.patient_id)), phone=_wrap(_str(f.patient.phone)),
        id_number=_wrap(_str(f.patient.id_number)),
    )
    rec.encounter = Encounter(
        type=_wrap(f.encounter.type), hospital=_wrap(f.encounter.hospital),
        department=_wrap(f.encounter.department), doctor=_wrap(f.encounter.doctor),
        admission_time=_wrap(f.encounter.admission_time),
        discharge_time=_wrap(f.encounter.discharge_time),
        length_of_stay_days=_wrap(_num(f.encounter.length_of_stay_days)),
        visit_time=_wrap(f.encounter.visit_time),
        insurance_type=_wrap(f.encounter.insurance_type),
    )
    rec.diagnoses = [
        Diagnosis(name=_wrap(d.name), type=_wrap(d.type), icd_code=_wrap(d.icd_code, 0.8),
                  is_primary=d.is_primary) for d in f.diagnoses
    ]
    rec.procedures = [
        Procedure(name=_wrap(p.name), date=_wrap(p.date), anesthesia=_wrap(p.anesthesia),
                  code=_wrap(p.code, 0.7), body_part=_wrap(p.body_part)) for p in f.procedures
    ]
    rec.medications = [_med(m) for m in f.medications]
    rec.costs = Cost(
        total=_wrap(_num(f.costs.total)), insurance_paid=_wrap(_num(f.costs.insurance_paid)),
        self_paid=_wrap(_num(f.costs.self_paid)), other_paid=_wrap(_num(f.costs.other_paid)),
        currency=_wrap(f.costs.currency, 1.0),
        breakdown=[CostItem(category=_wrap(c.category), amount=_wrap(_num(c.amount)),
                            insurance_class=_wrap(c.insurance_class), name=_wrap(c.name))
                   for c in f.costs.breakdown],
        insurance_class_summary=[CostItem(category=_wrap(c.category), amount=_wrap(_num(c.amount)))
                                 for c in f.costs.insurance_class_summary],
    )
    rec.labs_imaging = [
        LabFinding(name=_wrap(l.name), result=_wrap(l.result), date=_wrap(l.date)) for l in f.labs_imaging
    ]
    rec.discharge = Discharge(
        instructions=_wrap(f.discharge.instructions),
        follow_up=_wrap(f.discharge.follow_up),
        discharge_medications=[_med(m) for m in f.discharge.discharge_medications],
    )
    rec.timeline = [
        TimelineEvent(date=_wrap(t.date), type=_wrap(t.type), description=_wrap(t.description),
                      source=_wrap(t.source)) for t in f.timeline
    ]
    rec.extra = _wrap(f.extra, 0.8)
    return rec


def merge_rule_medications(rec: StructuredRecord, ocr_texts: List[dict], confidence: float = 0.7) -> StructuredRecord:
    """规则召回补缺：把 LLM 漏掉的药品名（来自费用清单/用药明细）补进 medications。

    对应 plan.txt：用药明细多为半结构化表格，正则优先抽取，LLM 补缺。
    """
    import difflib
    from . import rules

    def _n(s):
        return str(s).replace(" ", "").strip()

    existing = [_n(m.name.value) for m in rec.medications if m.name.value]

    def _is_dup(name):
        nn = _n(name)
        if nn in existing:
            return True
        # 模糊去重：OCR 错字导致的近重复（如 枸橡酸 vs 枸橼酸）视为同一条
        return bool(difflib.get_close_matches(nn, existing, n=1, cutoff=0.8))

    full = "\n".join(d["text"] for d in ocr_texts)
    for name in rules.extract_medication_names(full):
        if _is_dup(name):
            continue
        existing.append(_n(name))
        rec.medications.append(Medication(
            name=Field(value=name, confidence=confidence, method="rule", missing=False),
            category=Field(value="西药", confidence=0.7, method="rule"),
            spec=Field(value=None, confidence=0.0, missing=True),
            dose=Field(value=None, confidence=0.0, missing=True),
            frequency=Field(value=None, confidence=0.0, missing=True),
            route=Field(value=None, confidence=0.0, missing=True),
            course=Field(value=None, confidence=0.0, missing=True),
            purpose=Field(value=None, confidence=0.0, missing=True),
        ))
    return rec


def attach_evidence(rec: StructuredRecord, ocr_texts: List[dict]) -> StructuredRecord:
    """轻量证据溯源：对每个非空叶子值，在 OCR 原文里找含该值的行作为 snippet。

    只对"足够独特"的值（长度≥2 且能在原文唯一匹配）附证据，避免噪声。
    """
    def _find(v) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        if len(s) < 2:
            return None
        hits = []
        for d in ocr_texts:
            for line in d["text"].split("\n"):
                if s in line:
                    hits.append((d["source"], line.strip()))
        if not hits:
            return None
        src, line = hits[0]
        return f"{src}: {line[:80]}"

    def _walk(obj):
        if isinstance(obj, Field):
            if obj.value is not None and not obj.evidence:
                snip = _find(obj.value)
                if snip:
                    src = snip.split(": ", 1)[0]
                    obj.evidence.append(Evidence(source=src, snippet=snip.split(": ", 1)[1]))
                    obj.method = "llm+evidence"
            return
        if isinstance(obj, list):
            for it in obj:
                _walk(it)
            return
        if hasattr(obj, "__dict__"):
            for v in vars(obj).values():
                _walk(v)

    for field_name in ("patient", "encounter", "costs", "discharge",
                       "diagnoses", "procedures", "medications", "labs_imaging", "timeline"):
        _walk(getattr(rec, field_name))
    return rec
