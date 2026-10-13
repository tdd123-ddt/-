# -*- coding: utf-8 -*-
"""
模块 a · 病历结构化 JSON Schema（通用）

设计目标（对应 plan.txt 模块 a）：
1. 定义统一的病历结构化 JSON 契约，覆盖诊断(主/次/ICD)、手术(名称/术式/日期)、
   用药(通用名/剂量/疗程)、住院天数、费用构成(总费用/医保/自费/分类占比)等关键字段。
2. 面向"各种病例"通用：同时兼容住院/门诊/急诊、西医/中医、不同医院格式、不同医保体系。
   - 枚举用开放式字符串 + is_primary 等布尔位，避免写死取值；
   - 未知字段统一收进 extra，不丢信息；
   - 每条字段带 value/confidence/evidence，字段级置信度 + 证据溯源是硬要求。
3. 附带"医疗事件重建"（timeline），把散落在多份文档里的时间事件按时间轴重排。

字段级置信度约定：
- Field.value      抽取到的值（缺失时为 None，missing=True）
- Field.confidence 置信度 ∈ [0,1]，由「模型置信 + 规则交叉校验 + OCR 置信」综合得出
- Field.evidence   证据溯源：该值来自哪份文档、哪段原文（供模块 b 痛点溯源 / 展示二次脱敏用）
"""

from __future__ import annotations

from typing import List, Optional, Any, Literal
from pydantic import BaseModel, Field as PField


# ---------------------------------------------------------------------------
# 基础：证据与字段包装
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    """一处证据：来源文档 + 原文片段（脱敏后可展示）。"""
    source: str = ""                       # 来源文档名，如 "出院小结_出院记录_1"
    snippet: str = ""                      # 病历原文片段
    page: Optional[int] = None             # 页码（如有）


class Field(BaseModel):
    """带置信度与证据的字段（所有叶子字段统一用此包装）。"""
    value: Any = None                      # 抽取值（str/int/float/None）
    confidence: float = 0.0                # 字段级置信度 [0,1]
    evidence: List[Evidence] = PField(default_factory=list)
    missing: bool = False                  # 是否缺失（找不到）
    method: Optional[str] = None           # 来源：llm / rule / llm+rule / ocr

    @staticmethod
    def make(value, confidence=1.0, evidence=None, method=None) -> "Field":
        if evidence is None:
            evidence = []
        return Field(value=value, confidence=confidence,
                     evidence=evidence, missing=(value is None), method=method)


# ---------------------------------------------------------------------------
# 患者 / 就诊
# ---------------------------------------------------------------------------

class Patient(BaseModel):
    """患者基本信息（原型保留，展示前必须二次脱敏）。"""
    name: Field = PField(default_factory=Field)
    gender: Field = PField(default_factory=Field)        # 男/女
    age: Field = PField(default_factory=Field)           # 岁
    birth_date: Field = PField(default_factory=Field)    # 出生日期
    patient_id: Field = PField(default_factory=Field)    # 病案号/住院号/门诊号
    phone: Field = PField(default_factory=Field)         # 联系电话（脱敏）
    id_number: Field = PField(default_factory=Field)     # 身份证号（脱敏）


class Encounter(BaseModel):
    """一次就诊（住院/门诊/急诊）的元信息。"""
    type: Field = PField(default_factory=Field)          # 住院/门诊/急诊/24h入出院
    hospital: Field = PField(default_factory=Field)      # 医院名称
    department: Field = PField(default_factory=Field)    # 科室
    doctor: Field = PField(default_factory=Field)        # 主诊/主治医生
    admission_time: Field = PField(default_factory=Field)   # 入院时间（住院）
    discharge_time: Field = PField(default_factory=Field)   # 出院时间（住院）
    length_of_stay_days: Field = PField(default_factory=Field)  # 住院天数
    visit_time: Field = PField(default_factory=Field)    # 就诊时间（门诊）
    insurance_type: Field = PField(default_factory=Field)    # 医保类型（市医保/职工/居民/自费）


# ---------------------------------------------------------------------------
# 诊断 / 手术 / 用药
# ---------------------------------------------------------------------------

class Diagnosis(BaseModel):
    """一条诊断。type 覆盖主/次/入院/出院/初步/中医/西医等，用开放字符串避免写死。"""
    name: Field = PField(default_factory=Field)          # 诊断名称
    type: Field = PField(default_factory=Field)          # 主诊断/次诊断/入院诊断/出院诊断/初步诊断/中医/西医
    icd_code: Field = PField(default_factory=Field)      # ICD 编码（如有）
    is_primary: bool = False                             # 是否主诊断


class Procedure(BaseModel):
    """一条手术/操作。"""
    name: Field = PField(default_factory=Field)          # 手术/操作名称
    date: Field = PField(default_factory=Field)          # 手术日期
    anesthesia: Field = PField(default_factory=Field)    # 麻醉方式（全身麻醉/椎管内麻醉/局麻）
    code: Field = PField(default_factory=Field)          # 术式/收费编码（如有）
    body_part: Field = PField(default_factory=Field)     # 部位（如 右肩）


class Medication(BaseModel):
    """一条用药。兼容西药/中成药/中药饮片。"""
    name: Field = PField(default_factory=Field)          # 通用名（尽量归一，保留商品名）
    category: Field = PField(default_factory=Field)      # 西药/中成药/中药饮片/其他
    spec: Field = PField(default_factory=Field)          # 规格（如 0.5g/片、10ml:30mg）
    dose: Field = PField(default_factory=Field)          # 单次剂量
    frequency: Field = PField(default_factory=Field)     # 频次（如 每日二次、bid）
    route: Field = PField(default_factory=Field)         # 给药途径（口服/外用/静脉/坐浴）
    course: Field = PField(default_factory=Field)        # 疗程（如 1周、7剂）
    purpose: Field = PField(default_factory=Field)       # 用途（镇痛/抗感染/通便…）


# ---------------------------------------------------------------------------
# 费用
# ---------------------------------------------------------------------------

class CostItem(BaseModel):
    """费用构成中的一项（分类金额，或明细行）。"""
    category: Field = PField(default_factory=Field)      # 费用类别（西药费/材料费/手术费/床位费/检查费/检验费/治疗费/护理费/中药饮片费…）
    amount: Field = PField(default_factory=Field)        # 金额
    insurance_class: Field = PField(default_factory=Field)  # 医保分类（甲/乙/丙/自费/统筹…）
    name: Field = PField(default_factory=Field)          # 项目名（明细行时）


class Cost(BaseModel):
    """费用构成。不同医保体系字段不同，统一映射到下面三个口径 + 分类明细。"""
    total: Field = PField(default_factory=Field)         # 总费用
    insurance_paid: Field = PField(default_factory=Field)  # 医保支付（统筹/基金合计）
    self_paid: Field = PField(default_factory=Field)     # 个人支付/自费/自付
    other_paid: Field = PField(default_factory=Field)    # 补助/减免/救助等
    currency: Field = PField(default_factory=Field)      # 币种，默认 CNY
    breakdown: List[CostItem] = PField(default_factory=list)   # 分类金额构成
    insurance_class_summary: List[CostItem] = PField(default_factory=list)  # 甲/乙/丙类汇总（如有）


# ---------------------------------------------------------------------------
# 检查检验 / 出院 / 事件
# ---------------------------------------------------------------------------

class LabFinding(BaseModel):
    """一项检查/检验结论。"""
    name: Field = PField(default_factory=Field)          # 检查项目名（如 磁共振右肩关节平扫）
    result: Field = PField(default_factory=Field)        # 结论/结果
    date: Field = PField(default_factory=Field)          # 检查日期


class Discharge(BaseModel):
    """出院医嘱/出院带药/随访安排。"""
    instructions: Field = PField(default_factory=Field)  # 出院注意事项/医嘱全文
    follow_up: Field = PField(default_factory=Field)     # 复诊/随访安排
    discharge_medications: List[Medication] = PField(default_factory=list)  # 出院带药


class TimelineEvent(BaseModel):
    """医疗事件重建：一个带时间的事件。"""
    date: Field = PField(default_factory=Field)          # 事件日期时间
    type: Field = PField(default_factory=Field)          # 门诊就诊/入院/手术/检查/出院/复查/用药…
    description: Field = PField(default_factory=Field)   # 事件描述
    source: Field = PField(default_factory=Field)        # 来源文档


class DocumentInfo(BaseModel):
    """来源文档元信息（识别出的文档类型）。"""
    file: str = ""                                       # 原始文件名
    doc_type: Field = PField(default_factory=Field)      # 出院记录/出院小结/费用清单/门诊病历/检查报告/结算单/其它
    pages: int = 1


# ---------------------------------------------------------------------------
# 顶层
# ---------------------------------------------------------------------------

class StructuredRecord(BaseModel):
    """一份病历结构化后的完整 JSON。"""
    schema_version: str = "1.0"
    patient_id: str = ""                                 # 用于关联的目录/案件 ID
    patient: Patient = PField(default_factory=Patient)
    encounter: Encounter = PField(default_factory=Encounter)
    diagnoses: List[Diagnosis] = PField(default_factory=list)
    procedures: List[Procedure] = PField(default_factory=list)
    medications: List[Medication] = PField(default_factory=list)
    costs: Cost = PField(default_factory=Cost)
    labs_imaging: List[LabFinding] = PField(default_factory=list)
    discharge: Discharge = PField(default_factory=Discharge)
    timeline: List[TimelineEvent] = PField(default_factory=list)
    documents: List[DocumentInfo] = PField(default_factory=list)
    extra: Field = PField(default_factory=Field)         # 未覆盖信息兜底（不丢信息）

    def model_dump_json(self, **kw):
        return super().model_dump_json(**kw)


def json_schema() -> dict:
    """导出为 JSON Schema（供 Claude structured output / 校验使用）。"""
    return StructuredRecord.model_json_schema()


if __name__ == "__main__":
    import json
    print(json.dumps(json_schema(), ensure_ascii=False, indent=2))
