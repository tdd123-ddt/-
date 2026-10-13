# -*- coding: utf-8 -*-
"""
模块 a · 评估脚本（F1 + 关键字段缺失率）

目标（plan.txt）：F1 ≥ 0.90，关键字段缺失率 < 5%。

评估口径：
- 标量字段：归一化后精确匹配（日期/金额归一化）。
- 列表字段（诊断/手术/用药/检查/事件）：按"项目名"做集合级 F1（允许顺序无关）。
- 关键字段缺失率：confidence.key_field_status 的 present 占比。
"""

from __future__ import annotations

import re
from typing import List, Dict, Any, Tuple

from .schema import StructuredRecord, Field


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------

def norm(s: Any) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    s = s.replace("，", ",").replace("。", ".").replace("：", ":")
    s = re.sub(r"\s+", "", s)
    s = s.replace(" ", "").lower()
    # 归一化常见分类后缀：B超-I类 / B超Ⅱ级 等
    s = re.sub(r'[-—]?[iⅠⅡⅢ]{1,3}[类级]', '', s)
    return s


def norm_num(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("元", "")
    # 数字 + 单位后缀（如 "41岁"/"58岁"）→ 提取纯数字，与 float 归一
    try:
        return float(s)
    except ValueError:
        m = re.search(r'-?\d+(?:\.\d+)?', s)
        if m:
            return float(m.group(0))
        return s


# ---------------------------------------------------------------------------
# 标量字段匹配
# ---------------------------------------------------------------------------

def _date_part(s: str) -> Optional[str]:
    """从字符串里抽日期部分（YYYY-MM-DD 或 YYYY年MM月DD日），忽略时间。"""
    m = re.search(r'20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}', s)
    return m.group(0) if m else None


def scalar_match(gold: Field, pred: Field) -> bool:
    g, p = gold.value, pred.value
    if g is None and p is None:
        return True  # 双方都缺失 = 一致
    if g is None or p is None:
        return False
    # 数字类容忍 0.01 误差
    ng, np_ = norm_num(g), norm_num(p)
    if isinstance(ng, float) and isinstance(np_, float):
        return abs(ng - np_) < 0.01
    # 日期时间：只比较日期部分（"2025-12-30" vs "2025-12-30 07:58:19" 视为一致）
    dg, dp = _date_part(str(g)), _date_part(str(p))
    if dg and dp:
        return norm(dg) == norm(dp)
    a, b = norm(g), norm(p)
    if a == b:
        return True
    # 名称类软匹配：一方是另一方子串（如 "肛肠外科（大运河）" ⊇ "肛肠外科"）
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        return True
    return False


# ---------------------------------------------------------------------------
# 列表字段（按名称匹配）
# ---------------------------------------------------------------------------

def _item_names(items: List[Any]) -> List[str]:
    out = []
    for it in items:
        name = getattr(it, "name", None)
        v = name.value if isinstance(name, Field) else name
        # 一条里可能合并多个（如 "肛门瘘管切开术+肛管内括约肌切开术"），拆开再匹配
        for part in re.split(r'[+、，,；;]', str(v)):
            p = norm(part)
            if p:
                out.append(p)
    return out


def _name_match(a: str, b: str) -> bool:
    """名称匹配：精确相等，或一方是另一方子串（长度≥2，兼容规格/类目后缀差异）。"""
    if a == b:
        return True
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        return True
    return False


def compute_list_f1(gold: List[Any], pred: List[Any]) -> Tuple[float, float, float, int, int, int]:
    """集合级 F1（贪心一对一匹配，允许预测出额外/遗漏项，兼容名称变体）。"""
    g = _item_names(gold)
    p = _item_names(pred)
    used = [False] * len(p)
    tp = 0
    for gi in g:
        for j, pj in enumerate(p):
            if not used[j] and _name_match(gi, pj):
                used[j] = True
                tp += 1
                break
    fp = len(p) - tp
    fn = len(g) - tp
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return prec, rec, f1, tp, fp, fn


# ---------------------------------------------------------------------------
# 顶层评估
# ---------------------------------------------------------------------------

# 参与 F1 的字段：标量字段路径 + 列表字段路径
SCALAR_FIELDS = [
    "patient.name", "patient.gender", "patient.age",
    "encounter.type", "encounter.hospital", "encounter.department",
    "encounter.admission_time", "encounter.discharge_time", "encounter.length_of_stay_days",
    "costs.total", "costs.insurance_paid", "costs.self_paid",
]
LIST_FIELDS = ["diagnoses", "procedures", "medications", "labs_imaging"]


def _resolve(rec, path: str):
    cur = rec
    for part in path.split("."):
        cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def evaluate(gold: StructuredRecord, pred: StructuredRecord) -> Dict[str, Any]:
    """返回整体评估结果：字段级明细 + 宏观 F1 + 缺失率。"""
    rows = []

    # 标量字段
    scalar_correct = 0
    scalar_total = 0
    for p in SCALAR_FIELDS:
        g = _resolve(gold, p)
        pr = _resolve(pred, p)
        if not isinstance(g, Field) or not isinstance(pr, Field):
            continue
        scalar_total += 1
        ok = scalar_match(g, pr)
        scalar_correct += ok
        rows.append({"field": p, "kind": "scalar", "gold": g.value, "pred": pr.value,
                     "correct": ok, "precision": 1.0 if ok else 0.0,
                     "recall": 1.0 if ok else 0.0, "f1": 1.0 if ok else 0.0})

    # 列表字段
    list_prec_sum = list_rec_sum = list_f1_sum = 0.0
    list_n = 0
    for p in LIST_FIELDS:
        g = _resolve(gold, p)
        pr = _resolve(pred, p)
        if g is None or pr is None:
            continue
        prec, rec, f1, tp, fp, fn = compute_list_f1(g, pr)
        list_n += 1
        list_prec_sum += prec
        list_rec_sum += rec
        list_f1_sum += f1
        rows.append({"field": p, "kind": "list", "gold": _item_names(g), "pred": _item_names(pr),
                     "correct": None, "precision": prec, "recall": rec, "f1": f1,
                     "tp": tp, "fp": fp, "fn": fn})

    scalar_acc = scalar_correct / scalar_total if scalar_total else 0.0
    list_prec = list_prec_sum / list_n if list_n else 0.0
    list_rec = list_rec_sum / list_n if list_n else 0.0
    list_f1 = list_f1_sum / list_n if list_n else 0.0

    # 宏观 F1：标量(精确匹配)与列表(集合F1)的加权平均
    macro_f1 = (scalar_acc + list_f1) / 2.0 if (scalar_total and list_n) else \
               (scalar_acc if scalar_total else list_f1)

    # 缺失率（在 pred 上算）
    from . import confidence
    mr = confidence.missing_rate(pred)

    return {
        "macro_f1": round(macro_f1, 4),
        "scalar_accuracy": round(scalar_acc, 4),
        "list_f1": round(list_f1, 4),
        "list_precision": round(list_prec, 4),
        "list_recall": round(list_rec, 4),
        "missing_rate": round(mr, 4),
        "n_scalar": scalar_total,
        "n_list": list_n,
        "details": rows,
    }


def print_report(res: Dict[str, Any]) -> str:
    lines = [
        f"Macro F1           : {res['macro_f1']:.4f}  (目标 ≥ 0.90)",
        f"  scalar accuracy  : {res['scalar_accuracy']:.4f}",
        f"  list F1          : {res['list_f1']:.4f}",
        f"  list precision   : {res['list_precision']:.4f}",
        f"  list recall      : {res['list_recall']:.4f}",
        f"关键字段缺失率       : {res['missing_rate']:.4f}  (目标 < 0.05)",
        "",
        "字段明细：",
    ]
    for r in res["details"]:
        if r["kind"] == "scalar":
            mark = "✓" if r["correct"] else "✗"
            lines.append(f"  {mark} {r['field']:<38} gold={str(r['gold'])[:20]:<20} pred={str(r['pred'])[:20]}")
        else:
            lines.append(f"  - {r['field']:<38} F1={r['f1']:.3f} P={r['precision']:.3f} R={r['recall']:.3f}"
                         f" (tp={r['tp']} fp={r['fp']} fn={r['fn']})")
    return "\n".join(lines)
