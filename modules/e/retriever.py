# -*- coding: utf-8 -*-
"""
模块 e · 问答检索器（Retriever）

从 qa_pairs.jsonl 里按"主题 + 关键词"检索最相近的真实问答对，作为
「追问回答」和「预判式 FAQ」的依据。轻量、无外部依赖、可离线。

检索策略（两级）：
  1. 主题命中：query 命中的主题 -> 该主题下所有问答对作为候选池；
  2. 关键词打分：query 与候选 question 的关键词重叠度排序，取 top-k。
"""

from __future__ import annotations

import os
import re
import json
from typing import List, Dict, Optional

from .qa_base import TOPICS, OUT_DIR

_PAIRS: Optional[List[Dict]] = None


def _load_pairs() -> List[Dict]:
    global _PAIRS
    if _PAIRS is None:
        path = os.path.join(OUT_DIR, "qa_pairs.jsonl")
        _PAIRS = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        _PAIRS.append(json.loads(line))
    return _PAIRS


def _topic_of(text: str) -> str:
    for key, kws in TOPICS.items():
        if any(kw in text for kw in kws):
            return key
    return "其他"


def _keywords(text: str) -> set:
    """从文本提取关键词：命中主题关键词的 + 2~4 字中文片段。"""
    kws = set()
    for key, words in TOPICS.items():
        for w in words:
            if w in text:
                kws.add(w)
    # 补充 2 字中文片段（去掉纯数字/标点）
    for seg in re.findall(r"[\u4e00-\u9fa5]{2,4}", text):
        kws.add(seg)
    return kws


def _score(query_kws: set, cand_q: str) -> float:
    """query 与候选问题的关键词重叠度（Jaccard + 命中数加权）。"""
    cand_kws = _keywords(cand_q)
    if not query_kws or not cand_kws:
        return 0.0
    inter = len(query_kws & cand_kws)
    return inter / max(1.0, len(query_kws))


def retrieve(query: str, top_k: int = 5, topic: Optional[str] = None) -> List[Dict]:
    """检索最相近的 top-k 真实问答对。

    topic 可显式指定（预判式 FAQ 用），否则从 query 自动推断。
    """
    pairs = _load_pairs()
    if not pairs:
        return []

    q_topic = topic or _topic_of(query)
    # 候选池：优先同主题，其次全部
    cands = [p for p in pairs if p["topic"] == q_topic]
    if len(cands) < top_k:
        cands = pairs

    q_kws = _keywords(query)
    scored = []
    for p in cands:
        s = _score(q_kws, p["question"])
        # 同主题加分
        if p["topic"] == q_topic:
            s += 0.2
        scored.append((s, p))

    scored.sort(key=lambda x: -x[0])
    # 去重（同 question 只留一条）
    seen_q = set()
    out = []
    for s, p in scored:
        if p["question"] in seen_q:
            continue
        seen_q.add(p["question"])
        out.append({"question": p["question"], "answer": p["answer"],
                    "topic": p["topic"], "service_type": p["service_type"], "score": round(s, 3)})
        if len(out) >= top_k:
            break
    return out


def answer_question(query: str, top_k: int = 3) -> Dict:
    """追问入口：返回 top-k 真实问答对 + 主题，供 LLM 基于真实答案作答。"""
    hits = retrieve(query, top_k=top_k)
    return {
        "query": query,
        "topic": _topic_of(query),
        "evidence": hits,
        "grounded": len(hits) > 0,   # 是否有真实依据
    }


if __name__ == "__main__":
    # 自测
    for q in ["理赔需要哪些材料", "能垫付吗", "多久能报销到账", "多久复查"]:
        r = answer_question(q)
        print(f"\n=== Q: {q}（主题 {r['topic']}，命中 {len(r['evidence'])}）===")
        for e in r["evidence"][:3]:
            print(f"  [{e['score']}] Q: {e['question'][:40]}")
            print(f"        A: {e['answer'][:70]}")
