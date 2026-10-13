# -*- coding: utf-8 -*-
"""
模块 a · 结构化抽取层（LLM 结构化输出 + 规则兜底）

分层策略（对应 plan.txt）：
1. LLM 结构化抽取：把 OCR 文本 → JSON（Claude structured output，JSON Schema 约束）。
2. 规则兜底：半结构化表格（费用/日期/医保分类/文档类型）用 rules.py 优先抽取，
   LLM 抽取不到时用规则补缺，并对 LLM 结果做交叉校验。
3. 字段级置信度：模型置信 + 规则交叉校验 + OCR 置信，由 confidence.py 综合。

LLM 后端可插拔：默认 Claude（anthropic SDK），也支持直接注入"人工/离线"抽取结果
（便于没有 API key 时跑通演示，或把专家标注当作抽取结果回放评估）。
"""

from __future__ import annotations

import os
import re
import json
from typing import List, Optional, Dict, Any

from . import rules
from .schema import StructuredRecord, Field, Evidence, json_schema


def _load_system_prompt() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "prompts", "system_prompt.md")
    with open(p, encoding="utf-8") as f:
        return f.read()


def _extract_json_from_text(text: str) -> Dict[str, Any]:
    """从纯文本回复里提取 JSON 对象（兼容 ```json 围栏 / 前后杂散文字）。"""
    t = text.strip()
    # 去掉 markdown 围栏
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    # 找第一个 '{' 到最后一个 '}' 之间
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("回复中未找到 JSON 对象")
    return json.loads(t[start:end + 1])


class ClaudeExtractor:
    """通过 Anthropic 兼容 API 做结构化抽取。

    默认走 DeepSeek 的 Anthropic 兼容端点（base_url=https://api.deepseek.com/anthropic，
    model=deepseek-chat），也支持原生 Claude（设置 ANTHROPIC_MODEL 即可）。
    优先用 tool-use 结构化输出；若后端不支持 tool_use，自动回退到"纯 JSON 文本"模式。
    """

    model = "deepseek-chat"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 base_url: Optional[str] = None, max_tokens: int = 8192):
        from .config import api_config
        cfg = api_config()
        self.api_key = api_key or cfg["api_key"]
        self.base_url = base_url or cfg["base_url"]
        self.model = model or cfg["model"]
        self.max_tokens = max_tokens

    def _build_messages(self, ocr_texts: List[Dict[str, str]]) -> List[dict]:
        doc_blob = "\n\n".join(
            f"===== 文档：{d['source']} =====\n{d['text']}" for d in ocr_texts
        )
        user_msg = f"以下是 OCR 识别出的医疗文档文本（可能含错字/乱序，请结合上下文理解）：\n\n{doc_blob}"
        return [{"role": "user", "content": user_msg}]

    def extract(self, ocr_texts: List[Dict[str, str]]) -> Dict[str, Any]:
        """ocr_texts: [{"source": 文档名, "text": 文本}, ...] -> 结构化 dict。

        采用"纯 JSON 文本"模式 + 扁平 schema（见 flat_schema.py），
        对 DeepSeek/Claude 都稳定；返回可直接 model_validate 的 StructuredRecord dict。
        """
        import anthropic  # 延迟导入，避免无 SDK 环境报错

        if not self.api_key:
            raise RuntimeError("未设置 ANTHROPIC_API_KEY，无法调用 LLM 抽取。")

        client = anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url or None)
        msgs = self._build_messages(ocr_texts)

        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_load_system_prompt(),
            messages=msgs,
            extra_body={"temperature": 0},  # 确定性抽取；经 extra_body 透传给后端
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return _extract_json_from_text(block.text)
        raise RuntimeError("模型未返回文本结果")


def extract_structured(
    ocr_texts: List[Dict[str, str]],
    patient_id: str = "",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> StructuredRecord:
    """端到端：LLM 抽取 → 扁平转结构化 → 规则补缺 → 附证据。"""
    from .flat_schema import coerce_to_record, attach_evidence, merge_rule_medications
    ex = ClaudeExtractor(api_key=api_key, model=model, base_url=base_url)
    flat = ex.extract(ocr_texts)
    rec = coerce_to_record(flat, patient_id=patient_id)
    rec = merge_rule_medications(rec, ocr_texts)   # 半结构化药品名规则补缺
    rec = attach_evidence(rec, ocr_texts)
    return rec


class OfflineExtractor:
    """离线抽取：直接回放预先得到（或人工/专家标注）的结构化结果。"""

    def __init__(self, record: Optional[Dict[str, Any]] = None,
                 record_file: Optional[str] = None):
        self._record = record
        self._record_file = record_file

    def extract(self, ocr_texts: List[Dict[str, str]]) -> Dict[str, Any]:
        if self._record is not None:
            return self._record
        if self._record_file:
            with open(self._record_file, encoding="utf-8") as f:
                return json.load(f)
        raise RuntimeError("OfflineExtractor 未配置记录来源")


# ---------------------------------------------------------------------------
# 纯规则基线（LLM 不可用时的兜底，也用于交叉校验）
# ---------------------------------------------------------------------------

def rules_extract(ocr_texts: List[Dict[str, str]]) -> Dict[str, Any]:
    """从 OCR 文本里用规则抽取费用/日期/医保分类/文档类型等确定性强的字段。"""
    full = "\n".join(d["text"] for d in ocr_texts)

    facts: Dict[str, Any] = {
        "total_cost": rules.extract_total_cost(full),
        "insurance_class_summary": rules.extract_insurance_class_summary(full),
        "dates": rules.extract_dates(full),
        "pii": rules.find_pii(full),
        "doc_types": [{"source": d["source"], "type": rules.detect_doc_type(d["text"])}
                      for d in ocr_texts],
    }
    return facts


def make_extractor(backend: str = "claude", **kw) -> object:
    """工厂：backend ∈ {claude, offline, rules}。"""
    if backend == "claude":
        return ClaudeExtractor(**kw)
    if backend == "offline":
        return OfflineExtractor(**kw)
    if backend == "rules":
        return None  # 纯规则路径在 run.py 里单独处理
    raise ValueError(f"未知后端: {backend}")


def to_record(obj: Dict[str, Any]) -> StructuredRecord:
    """把 dict 校验成 StructuredRecord（保证 schema 一致）。"""
    return StructuredRecord.model_validate(obj)


if __name__ == "__main__":
    print(json.dumps(json_schema(), ensure_ascii=False, indent=2)[:2000])
