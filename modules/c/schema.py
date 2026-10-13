# -*- coding: utf-8 -*-
"""
模块 c · 温度指引生成 —— 输出 Schema

内容六要素（必含）：诊断通俗解释 / 治疗过程回顾 / 费用解读 / 康复计划 / 心理安抚 / 下次就医建议。
反模板要求：每部分必须锚定用户的具体数字/事件（住院天数、自费金额、手术名称、复查时间），
禁止通用科普。

输入：模块 a 结构化 JSON + 模块 b 痛点识别结果。
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field as PField


# 六要素 key
SIX_ELEMENTS = [
    ("diagnosis_plain", "诊断通俗解释"),
    ("treatment_review", "治疗过程回顾"),
    ("cost_explanation", "费用解读"),
    ("recovery_plan", "康复计划"),
    ("psychological", "心理安抚"),
    ("next_visit", "下次就医建议"),
]


class Section(BaseModel):
    """指引的一个要素段落。"""
    element: str = ""           # 六要素 key
    title: str = ""             # 段落标题（面向用户的口语化标题）
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
