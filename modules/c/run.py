# -*- coding: utf-8 -*-
"""
模块 c · 温度指引生成 端到端入口

输入：模块 a 结构化 JSON + 模块 b 痛点 JSON（同目录、同 <pid>）
输出：output/<pid>.guidance.json + output/<pid>.guidance.md（可读版）

用法：
    python -m modules.c.run output/CL0300004715289963.json
    python -m modules.c.run output/CL0300004715289963.json --blind-eval   # 生成 + 盲评
"""

from __future__ import annotations

import os
import sys
import json
import argparse
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.c.schema import Guidance
from modules.c.generate import generate_guidance, personalization_check, personalization_check_v2
from modules.c import evaluate


def _load(path: str) -> Dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(record_path: str, out_path: str = None, blind_eval: bool = False) -> Guidance:
    record = _load(record_path)
    pid = record.get("patient_id") or os.path.splitext(os.path.basename(record_path))[0]

    # 模块 b 痛点（可选，找不到就空）
    pain_path = os.path.join(os.path.dirname(os.path.abspath(record_path)),
                             f"{pid}.pain_points.json")
    pain_points = []
    if os.path.exists(pain_path):
        pain_points = _load(pain_path).get("pain_points", [])

    print(f"[1/3] 生成温度指引（病种：{'、'.join(str(d.get('name',{}).get('value')) for d in record.get('diagnoses',[]) or [])}）...")
    g = generate_guidance(record, pain_points)

    # 反模板自检
    chk = personalization_check(record, g)
    print(f"[2/3] 反模板自检：引用用户具体事实 {chk['cited_count']}/{chk['total_facts']} "
          f"(个性化分数 {chk['personalization_score']})")

    # 反模板自检 v2（标题相似度 + 句式多样性）
    chk2 = personalization_check_v2(record, g)
    print(f"      标题相似度 {chk2['title_similarity']}（越接近 0 越非模板，≥0.6 判模板化：{chk2['title_templated']}）")
    print(f"      句式多样性 {chk2['sentence_diversity']}（祈使式开头 {chk2['imperative_openings']}/{chk2['total_sections']}，"
          f"雷同开头：{chk2['uniform_opening']}）")
    print(f"      综合非模板度 {chk2['anti_template_score']}（0~1，越高越个性化）")

    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(record_path)), f"{pid}.guidance.json")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(g.model_dump_json(indent=2))
    # 可读版
    md_path = out_path.replace(".json", ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(g.full_text)

    print(f"[3/3] 输出：{out_path} 和 {md_path}")

    if blind_eval:
        print("盲评中（3 名模拟盲评员）...")
        res = evaluate.blind_evaluate(g)
        print(f"  温度感均分 {res['temperature_avg']} / 可行动性均分 {res['actionability_avg']} "
              f"/ 总均分 {res['overall_avg']}  (目标 ≥ 4/5)")
        res_path = out_path.replace(".guidance.json", ".guidance.scores.json")
        with open(res_path, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)

    print("\n" + g.full_text[:1200])
    return g


def main():
    ap = argparse.ArgumentParser(description="模块 c 温度指引生成")
    ap.add_argument("record_json", help="模块 a 结构化结果 JSON 路径")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--blind-eval", action="store_true", help="生成后跑盲评打分")
    args = ap.parse_args()
    run(args.record_json, out_path=args.out, blind_eval=args.blind_eval)


if __name__ == "__main__":
    main()
