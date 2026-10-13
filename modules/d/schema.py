# -*- coding: utf-8 -*-
"""
模块 d · 品质画像与推荐 —— 画像维度 + 输出 Schema

画像维度（≥5，对齐 plan.txt）：
1. cost_efficiency        费用效率（同病种均次费用）
2. length_of_stay         平均住院天数
3. self_pay_ratio         自费比例
4. medication_reasonableness 用药合理性（均次用药种数 / 重复用药率）
5. treatment_standard     治疗规范性（手术-诊断匹配 / 主要治疗符合常规）
6. readmission_rate       再入院率（同病种 30 天）
7. satisfaction           患者满意度

原则：**纯数据驱动**——画像每个维度都由聚合统计得出，每条推荐理由都能溯源到数据字段，
不靠 LLM 自由发挥（对应红线"品质画像脱离数据"）。
"""

from __future__ import annotations

from typing import List, Optional, Any
from pydantic import BaseModel, Field as PField


DIMENSIONS = [
    ("cost_efficiency", "费用效率", "同病种均次费用，越低越好"),
    ("length_of_stay", "平均住院天数", "越短越高效（同病种）"),
    ("self_pay_ratio", "自费比例", "越低对患者越友好"),
    ("medication_reasonableness", "用药合理性", "均次用药种数 / 重复用药率"),
    ("treatment_standard", "治疗规范性", "手术与诊断匹配度"),
    ("readmission_rate", "再入院率", "同病种 30 天再入院，越低越好"),
    ("satisfaction", "患者满意度", "患者评分，越高越好"),
]

# 每个维度的方向：lower=越低越好，higher=越高越好
DIRECTION = {
    "cost_efficiency": "lower",
    "length_of_stay": "lower",
    "self_pay_ratio": "lower",
    "medication_reasonableness": "higher",
    "treatment_standard": "higher",
    "readmission_rate": "lower",
    "satisfaction": "higher",
}


class DimensionValue(BaseModel):
    """一个画像维度的数值 + 溯源。"""
    dimension: str = ""              # key
    label: str = ""                  # 中文名
    value: float = 0.0               # 聚合值
    unit: str = ""                   # 单位（元/天/%/种/分）
    n_samples: int = 0               # 参与聚合的样本数
    direction: str = "lower"         # lower/higher
    source: str = ""                 # 溯源：如 "costs.total 均值"


class Profile(BaseModel):
    """一个 (机构 × 病种) 的品质画像。"""
    hospital: str = ""
    disease: str = ""
    n_cases: int = 0
    dimensions: List[DimensionValue] = PField(default_factory=list)
    synthetic: bool = False          # 是否为合成演示数据


class Recommendation(BaseModel):
    """一条可解释推荐。"""
    disease: str = ""                # 病种
    hospital: str = ""               # 推荐机构
    rank: int = 0                    # 排名
    score: float = 0.0               # 综合分（数据驱动）
    reasons: List[str] = PField(default_factory=list)   # ≥2 条可量化理由
    evidence: List[dict] = PField(default_factory=list)  # 每条理由溯源 {dimension, value, vs_mean}


class ProfileReport(BaseModel):
    """画像 + 推荐 汇总。"""
    profiles: List[Profile] = PField(default_factory=list)
    recommendations: List[Recommendation] = PField(default_factory=list)
