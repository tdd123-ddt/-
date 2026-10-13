# -*- coding: utf-8 -*-
"""
模块 d · 演示数据（确定性合成 + 真实病例折叠）

真实病例不足"每病种 5 家机构"时，用确定性合成数据补齐机构覆盖（synthetic=True 可识别）。
本文件**动态识别 output/ 里的所有真实病例**（不限固定 3 个），对每个病种：
- 已知病种（DISEASES 里有配置）用预置的机构名/药名；
- 未知病种（如肠胃炎）按真实病例的统计量（费用/天数/自费）自动推导同行机构与合成样本。

接入真实多机构数据后，替换 all_records 的输入即可，聚合/推荐逻辑不变。
"""

from __future__ import annotations

import os
import json
import random
from typing import List, Dict, Any

from .aggregate import record_features


# 已知病种配置：机构列表[0]=真实机构，费用区间，住院天数区间，自费比例区间，用药数区间，药名池
DISEASES = {
    "右肩关节肩袖损伤": {
        "hospitals": ["吉林大学第一医院", "吉林省人民医院", "长春市中心医院",
                      "吉林市中心医院", "延边大学附属医院"],
        "type": "住院", "cost": (25000, 45000), "los": (2, 5),
        "self_pay": (0.12, 0.35), "meds": (12, 30),
        "drugs": ["丙泊酚乳状注射液", "吸入用七氟烷", "注射用盐酸瑞芬太尼", "环泊酚注射液",
                  "注射用甲苯磺酸瑞马唑仑", "布洛芬注射液", "盐酸羟考酮注射液",
                  "枸橼酸舒芬太尼注射液", "注射用头孢拉定", "氯化钠注射液", "贝米肝素钠注射液"],
    },
    "肛瘘": {
        "hospitals": ["浙江大学医学院附属邵逸夫医院", "浙江省人民医院", "杭州市第一人民医院",
                      "温州市中心医院", "宁波市第一医院"],
        "type": "住院", "cost": (6000, 9500), "los": (1, 3),
        "self_pay": (0.35, 0.60), "meds": (6, 20),
        "drugs": ["氨基己酸针", "凝血酶", "维生素K1针", "尖吻蝮蛇血凝酶针",
                  "聚卡波非钙片", "氨酚羟考酮片", "克林霉素分散片", "醋酸氯己定溶液",
                  "萘普生钠氯化钠针", "吲哚美辛栓"],
    },
    "多囊卵巢综合征": {
        "hospitals": ["杭州市萧山区中医院", "浙江省中医院", "杭州市中医院",
                      "浙江中医药大学附属第二医院", "温州市中医院"],
        "type": "门诊", "cost": (120, 320), "los": (0, 0),
        "self_pay": (0.6, 1.0), "meds": (10, 20),
        "drugs": ["柴胡", "姜半夏", "黄芩", "白芍（麸）", "大枣", "甘草", "蒲公英",
                  "桔梗", "鱼腥草", "菟丝子", "芦根", "茜草"],
    },
}

# 未知病种的通用机构名 / 药名
GENERIC_HOSPITALS = ["市第一人民医院", "市中心医院", "市人民医院", "市第二人民医院", "市立医院"]
GENERIC_DRUGS = ["蒙脱石散", "奥美拉唑", "左氧氟沙星", "头孢克肟", "布洛芬", "对乙酰氨基酚",
                 "氯化钠注射液", "多潘立酮", "双歧杆菌", "盐酸小檗碱"]

# 每机构质量偏移（index 0 = 真实机构，较优）——制造机构间差异，让推荐有意义
# (费用系数, 自费偏移, 满意度, 再入院概率, 重复用药概率)
QUALITY = [
    (0.90, -0.04, 4.6, 0.02, 0.05),
    (1.05, +0.02, 4.2, 0.05, 0.10),
    (1.15, +0.05, 3.9, 0.08, 0.18),
    (1.30, +0.08, 3.5, 0.12, 0.25),
    (1.00, +0.00, 4.0, 0.06, 0.12),
]


def _F(v, conf=0.9):
    return {"value": v, "confidence": conf, "missing": False, "method": "synthetic", "evidence": []}


def _make_case(disease: str, hospital: str, rng: random.Random, quality, config: Dict) -> Dict[str, Any]:
    cost_factor, self_offset, sat, readm_prob, poly_prob = quality
    cost = rng.uniform(*config["cost"]) * cost_factor
    los = rng.randint(*config["los"]) if config["los"][1] > 0 else 0
    self_pay_ratio = min(0.99, max(0.05, rng.uniform(*config["self_pay"]) + self_offset))
    med_n = rng.randint(*config["meds"])
    pool = config["drugs"]
    meds = [{"name": _F(pool[i % len(pool)]), "category": _F("西药"), "purpose": _F(None, 0.0)}
            for i in range(med_n)]
    if rng.random() < poly_prob:
        for extra in ["凝血酶", "维生素K1针", "氨基己酸针"][:2]:
            meds.append({"name": _F(extra), "category": _F("西药"), "purpose": _F("止血")})
    readmission = 1 if rng.random() < readm_prob else 0
    satisfaction = round(min(5.0, max(3.0, sat + rng.uniform(-0.2, 0.2))), 1)
    return {
        "patient_id": f"syn-{disease[:2]}-{hospital[:2]}-{rng.randint(10000, 99999)}",
        "synthetic": True,
        "encounter": {"hospital": _F(hospital), "type": _F(config["type"]),
                      "length_of_stay_days": _F(float(los))},
        "diagnoses": [{"name": _F(disease), "type": _F("出院诊断"), "is_primary": True}],
        "medications": meds,
        "costs": {"total": _F(round(cost, 2)), "self_paid": _F(round(cost * self_pay_ratio, 2)),
                  "insurance_paid": _F(round(cost * (1 - self_pay_ratio), 2)),
                  "breakdown": [], "insurance_class_summary": []},
        "readmission_30d": readmission,
        "satisfaction": satisfaction,
    }


def load_real_records(output_dir: str) -> List[Dict[str, Any]]:
    """扫描 output/ 下所有结构化记录（不限固定病例）。"""
    real: List[Dict[str, Any]] = []
    if not os.path.isdir(output_dir):
        return real
    for fn in sorted(os.listdir(output_dir)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(output_dir, fn), encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            continue
        if isinstance(rec, dict) and "encounter" in rec and "diagnoses" in rec:
            rec["synthetic"] = False
            real.append(rec)
    return real


def _config_for(disease: str, recs: List[Dict]) -> Dict:
    """为某病种生成配置：已知病种用预置，未知病种按真实统计量推导。"""
    feats = [record_features(r) for r in recs]
    real_hospitals = list(dict.fromkeys(f["hospital"] for f in feats if f["hospital"]))

    if disease in DISEASES:
        known = DISEASES[disease]
        hospitals = real_hospitals + [h for h in known["hospitals"] if h not in real_hospitals]
        return {**known, "hospitals": hospitals[:5]}

    costs = [f["total_cost"] for f in feats if f["total_cost"] > 0] or [500.0]
    loss = [f["length_of_stay"] for f in feats]
    sp = [f["self_pay_ratio"] for f in feats]
    mean = lambda xs: sum(xs) / len(xs)
    hospitals = (real_hospitals + [h for h in GENERIC_HOSPITALS if h not in real_hospitals])[:5]
    return {
        "hospitals": hospitals,
        "type": "住院" if mean(loss) > 0 else "门诊",
        "cost": (mean(costs) * 0.8, mean(costs) * 1.25),
        "los": (max(0, int(mean(loss)) - 1), int(mean(loss)) + 2),
        "self_pay": (max(0.05, mean(sp) - 0.12), min(0.99, mean(sp) + 0.12)),
        "meds": (6, 18),
        "drugs": GENERIC_DRUGS,
    }


def all_records(output_dir: str, n_per_hospital: int = 10, seed: int = 42) -> List[Dict[str, Any]]:
    """真实 + 合成：真实病例折叠进对应机构，其余机构用合成补齐到 5 家。"""
    real = load_real_records(output_dir)
    rng = random.Random(seed)

    by_disease: Dict[str, List[Dict]] = {}
    for r in real:
        by_disease.setdefault(record_features(r)["disease"], []).append(r)

    syn: List[Dict[str, Any]] = []
    for disease, recs in by_disease.items():
        config = _config_for(disease, recs)
        for hi, hospital in enumerate(config["hospitals"][:5]):
            for _ in range(n_per_hospital):
                syn.append(_make_case(disease, hospital, rng, QUALITY[hi], config))
    return real + syn
