# -*- coding: utf-8 -*-
"""
模块 c · 温度指引生成（LLM 个性化生成 + 反模板自检）

核心（对应红线）：system prompt 强制"只基于该用户病历的字段/事件/费用说话，禁止通用科普"，
并引用具体数字；生成后再做"反模板自检"——检查指引里到底引用了多少用户的具体事实。
"""

from __future__ import annotations

import os
import re
import json
from typing import List, Dict, Any, Optional

from .schema import Guidance, Section, render_text, SIX_ELEMENTS


def _load_system_prompt() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "prompts", "system_prompt.md"), encoding="utf-8") as f:
        return f.read()


def _extract_json(text: str) -> Dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("回复中未找到 JSON")
    return json.loads(t[start:end + 1])


class LLMGenerator:
    """用 Anthropic 兼容端点（DeepSeek/Claude）生成温度指引。"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 base_url: Optional[str] = None, max_tokens: int = 4096):
        from modules.a.config import api_config
        cfg = api_config()
        self.api_key = api_key or cfg["api_key"]
        self.base_url = base_url or cfg["base_url"]
        self.model = model or cfg["model"]
        self.max_tokens = max_tokens

    def generate(self, record: Dict, pain_points: List[Dict]) -> Guidance:
        import anthropic
        if not self.api_key:
            raise RuntimeError("未设置 ANTHROPIC_API_KEY（检查项目根目录 .env）")

        client = anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url or None)

        # 紧凑输入：结构化关键字段 + 痛点
        compact = {
            "patient": record.get("patient", {}),
            "encounter": record.get("encounter", {}),
            "diagnoses": [d.get("name", {}) for d in record.get("diagnoses", []) or []],
            "procedures": [p.get("name", {}) for p in record.get("procedures", []) or []],
            "medications": [m.get("name", {}) for m in record.get("medications", []) or []],
            "costs": record.get("costs", {}),
            "discharge": record.get("discharge", {}),
            "timeline": record.get("timeline", []) or [],
            "pain_points": [{"name": p.get("name"), "severity": p.get("severity"),
                             "reason": p.get("reason")} for p in pain_points],
        }
        user_msg = "结构化病历与痛点 JSON：\n" + json.dumps(compact, ensure_ascii=False)

        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_load_system_prompt(),
            messages=[{"role": "user", "content": user_msg}],
            extra_body={"temperature": 0.6},  # 温度感需要一点表达变化，不全用 0
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                data = _extract_json(block.text)
                return self._to_guidance(record.get("patient_id", ""), data)
        raise RuntimeError("模型未返回指引")

    @staticmethod
    def _to_guidance(patient_id: str, data: Dict) -> Guidance:
        sections = []
        for s in data.get("sections", []) or []:
            sections.append(Section(element=s.get("element", ""),
                                    title=s.get("title", ""),
                                    content=s.get("content", "")))
        g = Guidance(
            patient_id=patient_id,
            title=data.get("title", ""),
            greeting=data.get("greeting", ""),
            sections=sections,
            cited_facts=data.get("cited_facts", []) or [],
        )
        g.full_text = render_text(g)
        return g


# ---------------------------------------------------------------------------
# 反模板自检：指引里到底引用了多少用户的具体事实
# ---------------------------------------------------------------------------

def extract_key_facts(record: Dict) -> List[str]:
    """从结构化病历里抽出"具体事实"清单，用于反模板自检。"""
    facts: List[str] = []

    def v(f):
        return f["value"] if isinstance(f, dict) and "value" in f else f

    def fmt(x):
        # 3.0 → "3"，避免 "3.0天" 这种和口语"3天"对不上的形式
        if isinstance(x, float) and x.is_integer():
            return str(int(x))
        return str(x)

    enc = record.get("encounter", {})
    for key, label in [("length_of_stay_days", "天"), ("admission_time", "入院"),
                       ("discharge_time", "出院"), ("department", "科")]:
        val = v(enc.get(key, {}))
        if val is not None:
            facts.append(f"{fmt(val)}{label}" if label in ("天",) else str(val))

    costs = record.get("costs", {})
    for key, label in [("total", "总费用"), ("insurance_paid", "医保"), ("self_paid", "自费")]:
        val = v(costs.get(key, {}))
        if val not in (None, "", 0):
            facts.append(f"{label}{val}")

    for d in record.get("diagnoses", []) or []:
        name = v(d.get("name", {}))
        if name:
            facts.append(str(name))
    for p in record.get("procedures", []) or []:
        name = v(p.get("name", {}))
        if name:
            facts.append(str(name))
    return facts


def _date_forms(s: str) -> List[str]:
    """把一个日期串展开成可能的口语表述（2025-12-03 → 12月3日 / 2025年12月3日）。"""
    forms = [s]
    m = re.match(r"20(\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = m.group(1), m.group(2).lstrip("0"), m.group(3).lstrip("0")
        forms += [f"{mo}月{d}日", f"{mo}月{d}", f"{y}年{mo}月{d}日"]
    return forms


def _fact_match(fact: str, text: str) -> bool:
    """判断一个事实是否在指引文本里出现（兼容口语化表述）。"""
    s = str(fact)
    # 日期：试各种口语形式
    if re.match(r"20\d{2}[-/.]", s):
        if any(f in text for f in _date_forms(s)):
            return True
    # 金额/数字：匹配 ≥3 位的数字串（31233.19 → 31233 / 31233.19）
    for n in re.findall(r"\d+(?:\.\d+)?", s):
        if len(re.sub(r"\D", "", n)) >= 3 and n in text:
            return True
    # 中文名称：模型会"说人话"改写诊断/手术，故按 2~3 字片段匹配（肩袖/滑膜/括约肌…）
    zh = re.findall(r"[一-龥]+", s)
    for run in zh:
        for width in (3, 2):
            for i in range(len(run) - width + 1):
                if run[i:i + width] in text:
                    return True
    if s in text or re.sub(r"^(总费用|医保|自费|入院|出院)", "", s) in text:
        return True
    return False


def personalization_check(record: Dict, guidance: Guidance) -> Dict[str, Any]:
    """反模板自检：检查指引引用了多少用户具体事实（金额/天数/日期/手术名/诊断名）。"""
    facts = extract_key_facts(record)
    text = re.sub(r"\s+", "", guidance.full_text or "")
    hits = [f for f in facts if _fact_match(f, text)]
    score = len(hits) / len(facts) if facts else 0.0
    return {
        "cited_count": len(hits),
        "total_facts": len(facts),
        "personalization_score": round(score, 3),
        "cited": hits,
        "uncited": [f for f in facts if f not in hits],
    }


def generate_guidance(record: Dict, pain_points: List[Dict]) -> Guidance:
    """端到端：LLM 生成指引。"""
    return LLMGenerator().generate(record, pain_points)
