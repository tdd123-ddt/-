# -*- coding: utf-8 -*-
"""
模块 a · 端到端运行入口

用法示例：
    # 对一个病人文件夹跑全流程（OCR → 抽取 → 置信度 → 输出 JSON）
    python -m modules.a.run data/CL0300004715289963 -o output --backend claude

    # 无 API key 时：用已抽取好的 JSON（离线回放）
    python -m modules.a.run data/CL0300004715289963 -o output --backend offline \
        --offline-json gold/CL0300004715289963.json

    # 纯规则基线（只抽取费用/日期/文档类型等确定性字段）
    python -m modules.a.run data/CL0300004715289963 -o output --backend rules
"""

from __future__ import annotations

import os
import sys
import json
import argparse
from typing import List, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.a import ocr, rules, extract, confidence, evaluate
from modules.a.schema import StructuredRecord


def discover_images(patient_dir: str) -> List[str]:
    exts = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")
    files = []
    for root, _, fs in os.walk(patient_dir):
        for f in sorted(fs):
            if f.lower().endswith(exts):
                files.append(os.path.join(root, f))
    return files


def ocr_patient(image_paths: List[str], engine=None) -> List[Dict[str, str]]:
    out = []
    for p in image_paths:
        lines = ocr.ocr_image(p, engine=engine)
        text = ocr.ocr_to_text(lines)
        out.append({"source": os.path.basename(p), "text": text, "path": p})
    return out


def build_rules_record(patient_id: str, ocr_texts: List[Dict[str, str]]) -> StructuredRecord:
    """纯规则基线：把规则能抽到的确定性字段装进 schema，其余留空 + 原文进 extra。"""
    from modules.a.schema import (Patient, Encounter, Cost, CostItem, Field,
                                  Evidence, DocumentInfo)
    facts = extract.rules_extract(ocr_texts)
    full = "\n".join(d["text"] for d in ocr_texts)

    def F(v, m="rule", conf=0.9, src=None, snip=""):
        return Field(value=v, confidence=conf, missing=(v is None), method=m,
                     evidence=[Evidence(source=src or "", snippet=snip)] if v is not None else [])

    # 日期：优先用"入院日期/出院日期"标签；否则用最早/最晚日期近似
    adm, dis = rules.extract_admission_discharge(full)
    if not adm or not dis:
        dates = [d[0] for d in facts["dates"]]
        adm = adm or (min(dates) if dates else None)
        dis = dis or (max(dates) if dates else None)
    los = rules.days_between(adm, dis) if adm and dis else None

    rec = StructuredRecord(patient_id=patient_id)
    rec.encounter.admission_time = F(adm, conf=0.8)
    rec.encounter.discharge_time = F(dis, conf=0.8)
    rec.encounter.length_of_stay_days = F(los, conf=0.7)
    rec.costs.total = F(facts["total_cost"], conf=0.8)
    for s in facts["insurance_class_summary"]:
        rec.costs.insurance_class_summary.append(
            CostItem(category=F(s["class"], snip=s["evidence"]),
                     amount=F(s["amount"], snip=s["evidence"])))
    rec.documents = [DocumentInfo(file=d["source"], doc_type=F(d["type"])) for d in facts["doc_types"]]
    rec.extra = F({"raw_ocr": "\n".join(d["text"] for d in ocr_texts)}, m="rule", conf=1.0)
    return rec


def run(patient_dir: str, out_dir: str, backend: str = "claude",
        offline_json: str = None, patient_id: str = None, engine=None) -> dict:
    pid = patient_id or os.path.basename(os.path.abspath(patient_dir))
    image_paths = discover_images(patient_dir)
    if not image_paths:
        raise RuntimeError(f"{patient_dir} 下未找到图片")

    print(f"[1/4] OCR {len(image_paths)} 张图片 ...")
    ocr_texts = ocr_patient(image_paths, engine=engine)

    print("[2/4] 抽取 ...")
    if backend == "rules":
        rec = build_rules_record(pid, ocr_texts)
    elif backend == "offline":
        ex = extract.OfflineExtractor(record_file=offline_json)
        rec = extract.to_record(ex.extract(ocr_texts))
        rec.patient_id = rec.patient_id or pid
    else:  # claude / deepseek 兼容端点
        rec = extract.extract_structured(ocr_texts, patient_id=pid)

    print("[3/4] 字段级置信度交叉校验 ...")
    rec = confidence.recompute_confidence(rec)

    print("[4/4] 保存结果 ...")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{pid}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(rec.model_dump_json(indent=2, exclude_none=False))

    mr = confidence.missing_rate(rec)
    print(f"\n输出：{out_path}")
    print(f"关键字段缺失率：{mr:.4f}")
    return {"record": rec, "out_path": out_path, "missing_rate": mr,
            "ocr_texts": ocr_texts}


def main():
    ap = argparse.ArgumentParser(description="模块 a 病历结构化")
    ap.add_argument("patient_dir", help="病人图片目录")
    ap.add_argument("-o", "--out", default="output", help="输出目录")
    ap.add_argument("--backend", choices=["claude", "offline", "rules"], default="claude")
    ap.add_argument("--offline-json", default=None, help="backend=offline 时读取的结构化 JSON")
    ap.add_argument("--patient-id", default=None)
    args = ap.parse_args()
    run(args.patient_dir, args.out, backend=args.backend,
        offline_json=args.offline_json, patient_id=args.patient_id)


if __name__ == "__main__":
    main()
