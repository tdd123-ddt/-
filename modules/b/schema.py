# -*- coding: utf-8 -*-
"""
模块 b · 多维痛点识别 —— 分类体系 + 输出 Schema

覆盖三类（临床/费用/体验）、≥5 类具体痛点，输出：标签 + 严重程度(高/中/低) + 证据溯源 + 可解释理由。
识别方法：规则引擎打底（费用/体验类确定性规则）+ LLM 语义判断（用药-诊断匹配、非必要检查等）。

输入：模块 a 的结构化 JSON（StructuredRecord 的 dict 形式）。
"""

from __future__ import annotations

from typing import List, Optional, Any
from pydantic import BaseModel, Field as PField


# ---------------------------------------------------------------------------
# 痛点分类体系（taxonomy）
# ---------------------------------------------------------------------------

# 每类：key -> (中文名, 所属大类, 识别方式 rule/llm/both, 默认严重度触发说明)
TAXONOMY = {
    # —— 临床类 ——
    "clinical_medication_mismatch": {
        "name": "用药与诊断不匹配", "category": "临床", "method": "llm",
        "desc": "用药与诊断/病情不符，或缺少应有药物（如该用未用、用了无关药）",
    },
    "clinical_over_examination": {
        "name": "非必要检查/过度检查", "category": "临床", "method": "both",
        "desc": "与诊断不匹配或超出必要范围的检查检验（如小手术配血、大量非常规化验）",
    },
    "clinical_over_treatment": {
        "name": "非必要手术/过度治疗", "category": "临床", "method": "llm",
        "desc": "手术/治疗与病情不匹配，或存在更保守可选方案",
    },
    "clinical_polypharmacy": {
        "name": "重复用药/用药种类过多", "category": "临床", "method": "rule",
        "desc": "同类药物重复使用或同时使用过多（如同时用≥3种止血药/镇痛药）",
    },
    # —— 费用类 ——
    "cost_self_pay_high": {
        "name": "自费比例异常", "category": "费用", "method": "rule",
        "desc": "自费/自付占总费用比例过高",
    },
    "cost_drug_ratio_high": {
        "name": "药品费用占比过高", "category": "费用", "method": "rule",
        "desc": "药品费占总费用比例过高",
    },
    # —— 体验类 ——
    "exp_jargon": {
        "name": "诊断/医嘱术语晦涩", "category": "体验", "method": "rule",
        "desc": "诊断或医嘱使用了患者难懂的专业术语，缺乏通俗解释",
    },
    "exp_lack_guidance": {
        "name": "病历缺乏康复/健康指导", "category": "体验", "method": "rule",
        "desc": "出院小结/门诊病历缺少可执行的康复、用药、生活指导",
    },
    "exp_lack_followup": {
        "name": "随访/复诊安排缺失", "category": "体验", "method": "rule",
        "desc": "缺少明确的复诊时间、随访安排",
    },
}

CATEGORIES = ["临床", "费用", "体验"]
SEVERITIES = ["高", "中", "低"]


# ---------------------------------------------------------------------------
# 规则阈值
# ---------------------------------------------------------------------------

THRESHOLDS = {
    # 自费比例（自费/总费用）；总费用低于此值不视为"异常"（门诊小额自费属常态）
    "self_pay": {"高": 0.60, "中": 0.40, "低": 0.25},
    "min_total_for_cost": 500.0,
    # 药品费占比
    "drug_ratio": {"高": 0.60, "中": 0.40, "低": 0.30},
    # 同类重复用药数
    "polypharmacy_count": 3,
    # 出院指导最小长度（字符），低于则视为"缺乏康复指导"
    "guidance_min_len": 30,
    # 化验/检查项目数（规则触发"可能过度检查"，LLM 再确认）
    "over_exam_count": 20,
}

# 术语晦涩词典（诊断里出现即提示"术语晦涩"，供规则打底，LLM 可细化）
JARGON_TERMS = [
    "肩袖", "滑膜", "半月板", "腱鞘", "关节腔", "骨质增生", "骨髓水肿",
    "多囊卵巢", "综合征", "肝郁", "肾虚", "气滞", "血瘀", "痰湿",
    "息肉", "腺瘤", "狭窄", "梗死", "粥样硬化", "斑块", "心肌", "冠脉",
    "支架", "射频", "消融",
]

# 同类用药关键词（用于规则判断"重复用药"）
DRUG_CLASS_KEYWORDS = {
    "止血": ["凝血", "血凝", "氨基己", "氨甲环酸", "维生素K1", "酚磺乙胺", "蛇毒血凝"],
    "镇痛(阿片/NSAID)": ["羟考酮", "布洛芬", "萘普生", "吲哚美辛", "双氯芬酸", "曲马多", "吗啡", "芬太尼", "舒芬太尼"],
    "麻醉镇静": ["丙泊酚", "环泊酚", "瑞马唑仑", "七氟烷", "咪达唑仑", "依托咪酯", "右美托咪定", "氯胺酮"],
    "抗感染": ["头孢", "克林霉素", "阿莫西林", "左氧氟沙星", "青霉素", "阿奇霉素", "甲硝唑"],
    "糖皮质激素": ["倍他米松", "地塞米松", "甲泼尼龙", "泼尼松", "氢化可的松"],
}


# ---------------------------------------------------------------------------
# 输出 Schema
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    """一处证据：定位到字段/文档 + 原文/数值片段。"""
    source: str = ""          # 字段路径或来源文档，如 "costs.self_paid" / "出院小结"
    snippet: str = ""         # 证据片段（原文或数值）


class PainPoint(BaseModel):
    """一条痛点。"""
    type: str                 # taxonomy key，如 "cost_self_pay_high"
    name: str                 # 中文名
    category: str             # 临床/费用/体验
    severity: str             # 高/中/低
    score: float = 0.0        # 置信/强度 0~1
    reason: str = ""          # 可解释理由（引用具体数字）
    evidence: List[Evidence] = PField(default_factory=list)
    method: str = "rule"      # rule / llm / rule+llm


class PainPointReport(BaseModel):
    """一个病例的痛点识别结果。"""
    patient_id: str = ""
    pain_points: List[PainPoint] = PField(default_factory=list)

    def model_dump_json(self, **kw):
        return super().model_dump_json(**kw)
