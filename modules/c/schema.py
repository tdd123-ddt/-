# -*- coding: utf-8 -*-
"""
模块 c · 温度指引生成 —— 输出 Schema

内容要素（element 键固定，供评估/结构化使用；标题由 LLM 口语化、锚定病人）。
反模板要求：每部分必须锚定用户的具体数字/事件（住院天数、自费金额、手术名称、复查时间），
禁止通用科普。

动态章节：sections 不强制"六要素六段"，LLM 按就诊类型（门诊/住院/手术/中医等）增减、
合并、拆分、调整侧重。element 键可来自下方要素池，也可自定义语义化 key（如 recovery_wound）。

输入：模块 a 结构化 JSON + 模块 b 痛点识别结果。
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field as PField


# 要素池（element key 固定，供评估/结构化使用；非"必含六段"模板）
# 顺序仅代表默认建议，实际 sections 由 LLM 按病人动态组织。
SIX_ELEMENTS = [
    ("diagnosis_plain", "诊断通俗解释"),
    ("treatment_review", "治疗过程回顾"),
    ("cost_explanation", "费用解读"),
    ("recovery_plan", "康复计划"),
    ("psychological", "心理安抚"),
    ("next_visit", "下次就医建议"),
]

# 扩展要素池（动态章节时可选用的语义化 key）
EXTRA_ELEMENTS = [
    ("recovery_wound", "伤口护理"),
    ("recovery_activity", "活动限制"),
    ("recovery_diet", "饮食建议"),
    ("diagnosis_tcm", "中医证型解读"),
]


class Section(BaseModel):
    """指引的一个要素段落。"""
    element: str = ""           # 要素 key（评估/结构化用，来自 SIX_ELEMENTS 或自定义）
    title: str = ""             # 段落标题（LLM 口语化、锚定病人病/部位/事件，禁止模板）
    content: str = ""           # 段落正文


class Guidance(BaseModel):
    """一份个性化温度指引。"""
    patient_id: str = ""
    title: str = ""             # 整份指引标题
    greeting: str = ""          # 开头问候（温度开场）
    sections: List[Section] = PField(default_factory=list)
    cited_facts: List[str] = PField(default_factory=list)   # 引用的具体事实（反模板自检）
    full_text: str = ""         # 拼接后的完整文本（用于盲评阅读）


def render_text(g: Guidance) -> str:
    """把指引渲染成给用户看的完整 markdown 文本。"""
    parts = [g.title, "", g.greeting, ""] if g.title or g.greeting else []
    for s in g.sections:
        parts.append(f"### {s.title}")
        parts.append(s.content)
        parts.append("")
    return "\n".join(parts).strip()
