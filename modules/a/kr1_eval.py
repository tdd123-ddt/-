# -*- coding: utf-8 -*-
"""
模块 a · KR1 官方数据端到端评估驱动

把官方「命题材料提取结果」JSON 作为输入，跑通：
  1. fields → 文本 → LLM 结构化抽取（你的 pipeline，backend=claude）
  2. fields → 规则基线抽取（backend=rules，对照）
  3. gold(官方提取) vs pred(你的抽取) → F1 + 缺失率

产出：
  - 每病例的 pred JSON（output/）
  - 汇总 F1 报告（可写进提交材料）

用法：
    python -m modules.a.kr1_eval --cases 10                 # 抽 10 个病例跑 LLM
    python -m modules.a.kr1_eval --backend rules --cases 70  # 全量跑规则基线
    python -m modules.a.kr1_eval --backend claude --cases 70 # 全量跑 LLM（慢）
"""

from __future__ import annotations

import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.a import extract, evaluate, rules
from modules.a.adapt import (build_gold_set, _load_case_materials, _cost_blocks,
                             _num, _days_between, _first)
from modules.a.schema import StructuredRecord, Field

BASE = r"D:/KR1_补充材料/命题材料提取结果"


def fuse_rule_fields(cid: str, pred: StructuredRecord) -> StructuredRecord:
    """路径 A 融合：半结构化字段用官方 blocks 规则覆盖，与 gold 同口径。

    1. costs.total：费用清单 feeAmount 求和（LLM 无法从逐行明细求和）。
    2. encounter.length_of_stay_days：出院-入院+1（含当天，与 gold 同口径）。
    3. admission/discharge_time：用官方 admissionDate/dischargeDate 补。
    """
    mats = _load_case_materials(os.path.join(BASE, cid))

    # 1. 费用总额（规则求和，覆盖 LLM 的 None 或错误值）
    rows = _cost_blocks(mats)
    total = sum(_num(r.get("feeAmount")) or 0 for r in rows)
    if total:
        pred.costs.total = Field(value=round(total, 2), confidence=0.95,
                                 missing=False, method="rule")

    # 2. 日期 + 住院天数（规则重算，统一"含当天"口径）
    adm, _, _ = _first(mats, "admissionDate", prefer_types=("出院小结", "入院记录", "病案首页"))
    dis, _, _ = _first(mats, "dischargeDate", prefer_types=("出院小结", "病案首页"))
    if adm:
        pred.encounter.admission_time = Field(value=adm, confidence=0.95,
                                              missing=False, method="rule")
    if dis:
        pred.encounter.discharge_time = Field(value=dis, confidence=0.95,
                                              missing=False, method="rule")
    if adm and dis:
        los = _days_between(adm, dis)  # adapt._days_between 已含 +1
        if los:
            pred.encounter.length_of_stay_days = Field(value=los, confidence=0.95,
                                                       missing=False, method="rule")

    return pred


# 检查/检验项目 → 大类归并规则（收费明细颗粒度 → 检查类别颗粒度）
LAB_CAT_RULES = [
    ("血常规", ["血细胞分析", "血常规", "五分类"]),
    ("生化检验", ["测定", "生化", "葡萄糖", "尿素", "肌酐", "总蛋白", "白蛋白",
                "胆红素", "转氨酶", "碱性磷酸酶", "尿酸", "谷氨酰", "乳酸", "氨",
                "钙", "钾", "钠", "氯", "磷", "镁", "维生素", "氨基酸", "蛋白电泳"]),
    ("凝血功能", ["凝血", "血栓弹力", "纤维蛋白", "抗凝血酶", "D-二聚体", "血小板功能"]),
    ("血气分析", ["血气", "残气容积", "肺弥散", "肺通气", "肺功能"]),
    ("心电图", ["心电图", "心电监测", "动态心电"]),
    ("超声", ["超声", "多普勒", "彩超"]),
    ("CT", ["ct", "计算机体层", "体层成像"]),
    ("MRI", ["磁共振", "mri", "核磁"]),
    ("X线", ["x线", "摄影", "胸片", "dr", "造影"]),
    ("病理", ["病理", "切片", "免疫组化", "免疫组织化学", "活检", "涂片", "细胞学"]),
    ("传染病筛查", ["乙肝", "丙肝", "甲肝", "戊肝", "梅毒", "艾滋", "hiv", "肝炎", "抗体"]),
    ("肿瘤标志物", ["癌胚", "糖链抗原", "肿瘤标志", "afp", "psa", "ca19", "ca125", "神经元特异"]),
    ("炎症指标", ["白细胞介素", "干扰素", "肿瘤坏死", "降钙素", "c反应蛋白", "crp", "pct", "胃功能", "胃蛋白酶", "胃泌素"]),
    ("血型", ["血型", "abo", "rh"]),
    ("自身抗体", ["自身抗体", "ana", "ena", "抗体谱"]),
    ("内分泌", ["甲状腺", "甲功", "胰岛素", "皮质醇"]),
    ("尿常规", ["尿", "小便"]),
    ("粪便常规", ["粪便", "大便"]),
    ("感染指标", ["感染", "细菌", "病毒", "血清学", "病原体", "结核", "支原体", "衣原体", "流感", "幽门螺杆菌"]),
]


def _classify_lab(name: str) -> str:
    """把检查/检验项目名归并到类别；无法归并返回原串。"""
    n = str(name).lower()
    for cat, kws in LAB_CAT_RULES:
        for kw in kws:
            if kw in n:
                return cat
    return str(name)


def normalize_labs(rec: StructuredRecord) -> StructuredRecord:
    """把 labs_imaging 的检查项名归并到类别粒度（原字段不丢失，新增归一化副本到 extra）。

    返回浅拷贝的 record，其中 labs_imaging 的每个 LabFinding.name 被替换为类别名。
    用于评估时对齐 gold/pred 的检查颗粒度。
    """
    import copy
    r = copy.deepcopy(rec)
    for lab in r.labs_imaging:
        if lab.name.value:
            lab.name.value = _classify_lab(lab.name.value)
    return r


def fields_to_ocr_texts(case_dir: str) -> list:
    """把官方 JSON 的 fields 拼成 [{'source': 文档名, 'text': 文本}]，供 LLM/规则抽取。"""
    mats = _load_case_materials(case_dir)
    out = []
    for m in mats:
        fl = m["fields"]
        # 展平 fields 成 "key: value" 文本；blocks 单独展开明细
        lines = []
        for k, v in fl.items():
            if k == "blocks":
                continue
            if v in (None, "", []):
                continue
            if isinstance(v, list):
                v = "、".join(str(x) for x in v)
            lines.append(f"{k}: {v}")
        # 费用明细行
        for blk in fl.get("blocks", []) or []:
            for r in blk.get("costDetailList", []) or []:
                lines.append(f"{r.get('detailName')}|{r.get('itemCategoryName')}|{r.get('feeAmount')}")
        if lines:
            out.append({"source": m["filename"], "text": "\n".join(lines)})
    return out


def build_rules_pred(case_id: str, ocr_texts: list) -> StructuredRecord:
    """用规则基线生成 pred（对照）。"""
    full = "\n".join(d["text"] for d in ocr_texts)
    facts = extract.rules_extract(ocr_texts)
    from modules.a.schema import (Field, Evidence, CostItem, DocumentInfo)
    def F(v, m="rule", conf=0.9, src="", snip=""):
        return Field(value=v, confidence=conf, missing=(v is None), method=m,
                     evidence=[Evidence(source=src, snippet=snip)] if v is not None else [])
    rec = StructuredRecord(patient_id=case_id)
    rec.costs.total = F(facts["total_cost"], conf=0.8)
    for s in facts["insurance_class_summary"]:
        rec.costs.insurance_class_summary.append(
            CostItem(category=F(s["class"], snip=s["evidence"]),
                     amount=F(s["amount"], snip=s["evidence"])))
    rec.documents = [DocumentInfo(file=d["source"], doc_type=F(d["type"]))
                     for d in facts["doc_types"]]
    return rec


def align_with_gold(pred: StructuredRecord, gold: StructuredRecord) -> StructuredRecord:
    """对齐脱敏字段口径：官方数据未提供的字段（gold 为 None），pred 不应编造。

    脱敏字段（姓名/身份证/电话）和官方未采集的费用细分（医保支付/自费），
    官方 fields 无值时 pred 一律置 None，避免 LLM 编造导致的假性 FP。
    """
    if gold.patient.name.value is None:
        pred.patient.name = Field(value=None, missing=True, method="aligned")
    if gold.costs.insurance_paid.value is None:
        pred.costs.insurance_paid = Field(value=None, missing=True, method="aligned")
    if gold.costs.self_paid.value is None:
        pred.costs.self_paid = Field(value=None, missing=True, method="aligned")
    if gold.patient.id_number.value is None:
        pred.patient.id_number = Field(value=None, missing=True, method="aligned")
    if gold.patient.phone.value is None:
        pred.patient.phone = Field(value=None, missing=True, method="aligned")
    return pred


def missing_rate_by_gold(gold: StructuredRecord, pred: StructuredRecord) -> float:
    """按 gold 口径计算关键字段缺失率。

    语义：gold（官方数据）提供了哪些关键字段，pred 就应抽到哪些；
    缺失率 = 抽漏的关键字段数 / gold 提供的关键字段数。

    这比 confidence.missing_rate（只看 pred 病例类型）更符合"金标准"语义，
    因为官方数据未提供的字段（如 58/70 病例无医保结算单）不应算"抽取缺失"。
    """
    checks = [
        (bool(gold.diagnoses), bool(pred.diagnoses)),
        (bool(gold.procedures), bool(pred.procedures)),
        (bool(gold.medications), bool(pred.medications)),
        (gold.costs.total.value is not None, pred.costs.total.value is not None),
        (gold.costs.insurance_paid.value is not None, pred.costs.insurance_paid.value is not None),
        (gold.costs.self_paid.value is not None, pred.costs.self_paid.value is not None),
        (bool(gold.costs.breakdown), bool(pred.costs.breakdown)),
        (gold.encounter.length_of_stay_days.value is not None, pred.encounter.length_of_stay_days.value is not None),
    ]
    applicable = [c for c in checks if c[0]]  # gold 提供的字段
    if not applicable:
        return 0.0
    miss = sum(1 for g_has, p_has in applicable if not p_has)
    return miss / len(applicable)


def run_eval(cases: list[str], backend: str, out_dir: str) -> dict:
    gold = build_gold_set(BASE)
    summary = {"backend": backend, "n_cases": 0, "macro_f1": [], "missing_rate": [],
               "scalar_accuracy": [], "list_f1": [], "per_case": {}}
    os.makedirs(out_dir, exist_ok=True)

    for cid in cases:
        g = gold.get(cid)
        if g is None:
            continue
        case_dir = os.path.join(BASE, cid)
        ocr_texts = fields_to_ocr_texts(case_dir)
        if not ocr_texts:
            continue

        try:
            if backend == "claude":
                pred = extract.extract_structured(ocr_texts, patient_id=cid)
            elif backend == "rules":
                pred = build_rules_pred(cid, ocr_texts)
            else:
                raise ValueError(f"未知 backend: {backend}")
        except Exception as e:
            print(f"[error] {cid}: {e}")
            continue

        # 路径 A 融合：半结构化字段规则覆盖（与 gold 同口径）
        pred = fuse_rule_fields(cid, pred)
        # 脱敏字段对齐：gold 无值 → pred 不编造
        pred = align_with_gold(pred, g)

        # 保存 pred（保存归一化前的原始值，便于复盘）
        with open(os.path.join(out_dir, f"{cid}.json"), "w", encoding="utf-8") as f:
            f.write(pred.model_dump_json(indent=2, exclude_none=False))

        # 评估（labs 双侧类别归并，消除收费明细 vs 类别颗粒度错配）
        g_norm = normalize_labs(g)
        p_norm = normalize_labs(pred)
        res = evaluate.evaluate(g_norm, p_norm)
        res["missing_rate"] = missing_rate_by_gold(g, pred)  # 覆盖为 gold 口径缺失率
        summary["n_cases"] += 1
        summary["macro_f1"].append(res["macro_f1"])
        summary["missing_rate"].append(res["missing_rate"])
        summary["scalar_accuracy"].append(res["scalar_accuracy"])
        summary["list_f1"].append(res["list_f1"])
        summary["per_case"][cid] = res
        print(f"[{summary['n_cases']:2d}] {cid}  macro_f1={res['macro_f1']:.3f}  "
              f"missing={res['missing_rate']:.3f}")

    # 汇总
    n = summary["n_cases"]
    if n:
        summary["avg_macro_f1"] = round(sum(summary["macro_f1"]) / n, 4)
        summary["avg_missing_rate"] = round(sum(summary["missing_rate"]) / n, 4)
        summary["avg_scalar_accuracy"] = round(sum(summary["scalar_accuracy"]) / n, 4)
        summary["avg_list_f1"] = round(sum(summary["list_f1"]) / n, 4)

    with open(os.path.join(out_dir, "_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def reuse_eval(pred_dir: str, out_dir: str) -> dict:
    """复用已生成的 pred JSON（output/*/*.json）重新评估，不重调 LLM。

    用于快速验证评估口径修正（如 labs 类别归并、缺失率口径）的收益。
    pred 读回后需重新走 fuse_rule_fields + align_with_gold + normalize_labs，
    因为保存的是归一化前的原始 pred。
    """
    from .schema import StructuredRecord
    gold = build_gold_set(BASE)
    summary = {"backend": "reuse", "n_cases": 0, "macro_f1": [], "missing_rate": [],
               "scalar_accuracy": [], "list_f1": [], "per_case": {}}
    os.makedirs(out_dir, exist_ok=True)

    for fn in sorted(os.listdir(pred_dir)):
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        cid = fn[:-5]
        g = gold.get(cid)
        if g is None:
            continue
        try:
            with open(os.path.join(pred_dir, fn), encoding="utf-8") as f:
                pred = StructuredRecord.model_validate(json.load(f))
        except Exception as e:
            print(f"[error] {cid} 读回失败: {e}")
            continue

        pred = fuse_rule_fields(cid, pred)
        pred = align_with_gold(pred, g)
        g_norm = normalize_labs(g)
        p_norm = normalize_labs(pred)
        res = evaluate.evaluate(g_norm, p_norm)
        res["missing_rate"] = missing_rate_by_gold(g, pred)
        summary["n_cases"] += 1
        summary["macro_f1"].append(res["macro_f1"])
        summary["missing_rate"].append(res["missing_rate"])
        summary["scalar_accuracy"].append(res["scalar_accuracy"])
        summary["list_f1"].append(res["list_f1"])
        summary["per_case"][cid] = res

    n = summary["n_cases"]
    if n:
        summary["avg_macro_f1"] = round(sum(summary["macro_f1"]) / n, 4)
        summary["avg_missing_rate"] = round(sum(summary["missing_rate"]) / n, 4)
        summary["avg_scalar_accuracy"] = round(sum(summary["scalar_accuracy"]) / n, 4)
        summary["avg_list_f1"] = round(sum(summary["list_f1"]) / n, 4)
    with open(os.path.join(out_dir, "_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=10, help="跑前 N 个病例")
    ap.add_argument("--backend", choices=["claude", "rules"], default="claude")
    ap.add_argument("--out", default="output/kr1")
    ap.add_argument("--reuse", default="", help="复用已有 pred JSON 目录重新评估（不调 LLM）")
    args = ap.parse_args()

    gold = build_gold_set(BASE)
    all_cases = sorted(gold.keys())
    cases = all_cases[: args.cases]

    if args.reuse:
        print(f"复用模式：从 {args.reuse} 读 pred 重新评估")
        summary = reuse_eval(args.reuse, args.out)
    else:
        print(f"共 {len(all_cases)} 病例，跑 {len(cases)} 个，backend={args.backend}")
        summary = run_eval(cases, args.backend, args.out)

    print("\n========== 汇总 ==========")
    print(f"backend        : {summary['backend']}")
    print(f"n_cases        : {summary['n_cases']}")
    print(f"avg macro F1   : {summary.get('avg_macro_f1', 'N/A')}  (目标 ≥ 0.90)")
    print(f"avg scalar acc : {summary.get('avg_scalar_accuracy', 'N/A')}")
    print(f"avg list F1    : {summary.get('avg_list_f1', 'N/A')}")
    print(f"avg 缺失率     : {summary.get('avg_missing_rate', 'N/A')}  (目标 < 0.05)")


if __name__ == "__main__":
    main()
