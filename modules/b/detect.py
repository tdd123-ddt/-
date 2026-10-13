# -*- coding: utf-8 -*-
"""
模块 b · LLM 语义判断 + 规则/LLM 融合

- LLM 判断临床类语义痛点（用药-诊断匹配、非必要检查/手术），并可补充规则漏掉的痛点。
- 融合：规则产出 + LLM 产出 → 按 type 去重（同 type 保留置信/严重度更高者，合并证据）。

后端走 DeepSeek 的 Anthropic 兼容端点（复用 modules/a/config.py 的 .env 配置）。
"""

from __future__ import annotations

import os
import re
import json
from typing import List, Dict, Any, Optional

from .schema import PainPoint, Evidence, TAXONOMY
from . import rules


def _load_system_prompt() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "prompts", "system_prompt.md"), encoding="utf-8") as f:
        return f.read()


def _extract_json(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("回复中未找到 JSON 数组")
    return json.loads(t[start:end + 1])


class LLMDetector:
    """LLM 语义痛点判断。"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 base_url: Optional[str] = None, max_tokens: int = 4096):
        from modules.a.config import api_config
        cfg = api_config()
        self.api_key = api_key or cfg["api_key"]
        self.base_url = base_url or cfg["base_url"]
        self.model = model or cfg["model"]
        self.max_tokens = max_tokens

    def detect(self, record: Dict) -> List[PainPoint]:
        import anthropic
        if not self.api_key:
            raise RuntimeError("未设置 ANTHROPIC_API_KEY（检查项目根目录 .env）")

        client = anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url or None)
        # 只送关键字段，压缩上下文、聚焦判断
        compact = {
            "encounter": record.get("encounter", {}),
            "diagnoses": [d.get("name", {}) for d in record.get("diagnoses", []) or []],
            "procedures": [p.get("name", {}) for p in record.get("procedures", []) or []],
            "medications": [{"name": m.get("name", {}), "purpose": m.get("purpose", {})}
                            for m in record.get("medications", []) or []],
            "costs": record.get("costs", {}),
            "labs_imaging": record.get("labs_imaging", []) or [],
            "discharge": record.get("discharge", {}),
        }
        user_msg = "以下是结构化病历 JSON：\n" + json.dumps(compact, ensure_ascii=False)

        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_load_system_prompt(),
            messages=[{"role": "user", "content": user_msg}],
            extra_body={"temperature": 0},
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                raw = _extract_json(block.text)
                return [self._to_painpoint(x) for x in raw if isinstance(x, dict)]
        return []

    # LLM 只负责临床类语义判断（费用/体验/重复用药由规则处理），超出范围一律丢弃
    CLINICAL_TYPES = {"clinical_medication_mismatch",
                      "clinical_over_examination",
                      "clinical_over_treatment"}

    @classmethod
    def _to_painpoint(cls, x: Dict) -> Optional[PainPoint]:
        ptype = str(x.get("type", ""))
        if ptype not in cls.CLINICAL_TYPES:
            return None
        t = TAXONOMY[ptype]
        ev = [Evidence(source=str(e.get("source", "")), snippet=str(e.get("snippet", "")))
              for e in x.get("evidence", []) or []]
        sev = x.get("severity", "中")
        if sev not in ("高", "中", "低"):
            sev = "中"
        return PainPoint(type=ptype, name=t["name"], category=t["category"],
                         severity=sev, score=0.85, reason=str(x.get("reason", "")),
                         evidence=ev, method="llm")


def merge(rule_pps: List[PainPoint], llm_pps: List[PainPoint]) -> List[PainPoint]:
    """规则 + LLM 融合：同 type 去重，保留更完整者并合并证据。"""
    by_type: Dict[str, PainPoint] = {}
    for p in rule_pps + llm_pps:
        if p is None:
            continue
        cur = by_type.get(p.type)
        if cur is None:
            by_type[p.type] = p
            continue
        # 合并证据（去重）
        seen = {(e.source, e.snippet) for e in cur.evidence}
        for e in p.evidence:
            if (e.source, e.snippet) not in seen:
                cur.evidence.append(e)
                seen.add((e.source, e.snippet))
        # 取更高严重度 / 更高分
        order = {"高": 2, "中": 1, "低": 0}
        if order.get(p.severity, 0) > order.get(cur.severity, 0):
            cur.severity = p.severity
        if p.score > cur.score:
            cur.score = p.score
            cur.reason = p.reason
        if p.method == "llm" and cur.method == "rule":
            cur.method = "rule+llm"
        elif p.method == "rule" and cur.method == "llm":
            cur.method = "rule+llm"
    # 按 严重度 > 大类 排序
    order = {"高": 0, "中": 1, "低": 2}
    return sorted(by_type.values(), key=lambda p: (order.get(p.severity, 1), p.category))


def detect_all(record: Dict, use_llm: bool = True) -> List[PainPoint]:
    """规则 + LLM 全流程痛点识别。"""
    rule_pps = rules.detect_rules(record)
    llm_pps = []
    if use_llm:
        try:
            llm_pps = LLMDetector().detect(record)
        except Exception as e:
            print(f"[warn] LLM 痛点判断失败，退回纯规则：{type(e).__name__} {str(e)[:120]}")
    return merge(rule_pps, llm_pps)
