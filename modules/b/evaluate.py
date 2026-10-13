# -*- coding: utf-8 -*-
"""
模块 b · 评估（与专家标注的一致率）

一致率口径（对每题：识别出的痛点集合 vs 专家标注集合）：
- 标签级 F1：按 (type, severity) 或 type 做集合匹配，兼顾"漏报"与"误报"。
- 严重程度一致率：对共同识别出的 type，看 severity 是否一致。
- 目标：与专家标注一致率 ≥ 85%（以标签 F1 为主指标）。

输入：pred = 检测结果 dict（含 pain_points），gold = 专家标注 dict（同结构）。
"""

from __future__ import annotations

from typing import List, Dict, Any, Tuple


def _type_key(p) -> str:
    return p.get("type", "")


def label_f1(gold: List[Dict], pred: List[Dict]) -> Tuple[float, float, float, int, int, int]:
    """按 type 计算集合级 F1（允许额外/遗漏）。"""
    g = {_type_key(p) for p in gold}
    p = {_type_key(p) for p in pred}
    tp = len(g & p)
    fp = len(p - g)
    fn = len(g - p)
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return prec, rec, f1, tp, fp, fn


def severity_agreement(gold: List[Dict], pred: List[Dict]) -> float:
    """对共同识别的 type，看严重程度一致比例。"""
    g_map = {_type_key(p): p.get("severity") for p in gold}
    p_map = {_type_key(p): p.get("severity") for p in pred}
    common = set(g_map) & set(p_map)
    if not common:
        return 1.0
    agree = sum(1 for t in common if g_map[t] == p_map[t])
    return agree / len(common)


def evaluate(gold: List[Dict], pred: List[Dict]) -> Dict[str, Any]:
    """返回一致率报告。"""
    prec, rec, f1, tp, fp, fn = label_f1(gold, pred)
    sev_agree = severity_agreement(gold, pred)
    g = {_type_key(p) for p in gold}
    p = {_type_key(p) for p in pred}
    return {
        "label_precision": round(prec, 4),
        "label_recall": round(rec, 4),
        "label_f1": round(f1, 4),
        "severity_agreement": round(sev_agree, 4),
        "tp": tp, "fp": fp, "fn": fn,
        "missing": sorted(g - p),
        "extra": sorted(p - g),
    }


def print_report(res: Dict[str, Any]) -> str:
    lines = [
        f"标签 F1       : {res['label_f1']:.4f}  (一致率目标 ≥ 0.85)",
        f"  精确率       : {res['label_precision']:.4f}",
        f"  召回率       : {res['label_recall']:.4f}",
        f"严重程度一致率 : {res['severity_agreement']:.4f}",
        f"tp={res['tp']} fp={res['fp']} fn={res['fn']}",
    ]
    if res["missing"]:
        lines.append("  漏报: " + ", ".join(res["missing"]))
    if res["extra"]:
        lines.append("  误报: " + ", ".join(res["extra"]))
    return "\n".join(lines)
