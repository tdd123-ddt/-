# -*- coding: utf-8 -*-
"""
模块 b · 痛点识别 端到端入口

输入：模块 a 的结构化 JSON（output/<patient_id>.json）
输出：output/<patient_id>.pain_points.json（痛点标签 + 严重程度 + 证据溯源）

用法：
    python -m modules.b.run output/CL0300004715289963.json
    python -m modules.b.run output/CL0300004715289963.json --no-llm   # 纯规则
"""

from __future__ import annotations

import os
import sys
import json
import argparse
from typing import List, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.b.schema import PainPointReport
from modules.b.detect import detect_all


def load_record(path: str) -> Dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(record_path: str, out_path: str = None, use_llm: bool = True) -> PainPointReport:
    record = load_record(record_path)
    pid = record.get("patient_id") or os.path.splitext(os.path.basename(record_path))[0]

    print(f"[1/2] 痛点识别（{'规则+LLM' if use_llm else '纯规则'}）...")
    pps = detect_all(record, use_llm=use_llm)

    report = PainPointReport(patient_id=pid, pain_points=pps)

    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(record_path)),
                                f"{pid}.pain_points.json")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=2))

    print(f"[2/2] 输出：{out_path}")
    print(f"\n共识别 {len(pps)} 个痛点：")
    for p in pps:
        print(f"  [{p.severity}] {p.category}·{p.name}  <- {p.reason[:60]}")
    return report


def main():
    ap = argparse.ArgumentParser(description="模块 b 痛点识别")
    ap.add_argument("record_json", help="模块 a 的结构化结果 JSON 路径")
    ap.add_argument("-o", "--out", default=None, help="输出路径（默认同目录 <pid>.pain_points.json）")
    ap.add_argument("--no-llm", action="store_true", help="只用规则，不调 LLM")
    args = ap.parse_args()
    run(args.record_json, out_path=args.out, use_llm=not args.no_llm)


if __name__ == "__main__":
    main()
