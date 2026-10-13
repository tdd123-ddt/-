# -*- coding: utf-8 -*-
"""
模块 c · 盲评（温度感 + 可行动性，5 分制）

plan.txt 要求：设计 5 分制打分表（温度感、可行动性各 5 分），盲评平均 ≥ 4/5。
这里用"LLM 模拟盲评员"做初筛（跑 3 次取平均，模拟 3 个盲评员），人工复核作为最终把关。
"""

from __future__ import annotations

import os
import re
import json
from typing import List, Dict, Any, Optional

from .schema import Guidance, render_text


def _load_rubric() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "prompts", "rubric.md"), encoding="utf-8") as f:
        return f.read()


def _extract_json(text: str) -> Dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    return json.loads(t[start:end + 1]) if start != -1 else {}


class BlindReviewer:
    """用 LLM 模拟一名盲评员。"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 base_url: Optional[str] = None):
        from modules.a.config import api_config
        cfg = api_config()
        self.api_key = api_key or cfg["api_key"]
        self.base_url = base_url or cfg["base_url"]
        self.model = model or cfg["model"]

    def score(self, text: str) -> Dict[str, Any]:
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url or None)
        resp = client.messages.create(
            model=self.model,
            max_tokens=512,
            system=_load_rubric(),
            messages=[{"role": "user", "content": "请为下面这份健康指引打分：\n\n" + text}],
            extra_body={"temperature": 0.7},  # 模拟不同盲评员的主观差异
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return _extract_json(block.text)
        return {}


def blind_evaluate(guidance: Guidance, n_reviewers: int = 3) -> Dict[str, Any]:
    """对一份指引跑 n 名模拟盲评员，返回温度感/可行动性均分。"""
    text = guidance.full_text or render_text(guidance)
    temps, acts = [], []
    comments = []
    for i in range(n_reviewers):
        r = BlindReviewer().score(text)
        t = float(r.get("temperature", 0))
        a = float(r.get("actionability", 0))
        temps.append(t)
        acts.append(a)
        comments.append(r.get("comment", ""))
    return {
        "temperature_avg": round(sum(temps) / n_reviewers, 2),
        "actionability_avg": round(sum(acts) / n_reviewers, 2),
        "overall_avg": round((sum(temps) + sum(acts)) / (2 * n_reviewers), 2),
        "temperature_scores": temps,
        "actionability_scores": acts,
        "comments": comments,
    }
