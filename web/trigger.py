# -*- coding: utf-8 -*-
"""
理赔触发机制 —— 事件驱动入口

把"手动上传图片"升级为"理赔结案事件自动触发"：
1. 从 output/ 读取已结构化的真实案件（模块 a 的产出）；
2. 模拟理赔核心系统推送「claim.closed」结案事件；
3. 规则层做触发信号判定（自费异常 / 高额住院 / 复查窗口 / 反复就医）；
4. 自动跑完 b→c→d 全链路，输出附带触发原因的结果。

触发信号判定锚定 KR1 结构化字段，体现各模块串成一条链。
"""
import os
import json
import datetime
import glob
from typing import Dict, List, Any


def _val(f):
    """从 Field 包装 {"value": ...} 或裸值取值。"""
    return f.get("value") if isinstance(f, dict) and "value" in f else f


def load_cases(output_dir: str = "output") -> List[Dict]:
    """扫描 output/ 顶层已结构化的真实案件，返回 [(case_id, 诊断摘要, 路径)]。"""
    cases = []
    for path in sorted(glob.glob(os.path.join(output_dir, "CL*.json"))):
        try:
            rec = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        # 只要结构化病历（模块 a 产出），跳过 guidance / pain_points / scores 等衍生文件
        if not isinstance(rec, dict) or "encounter" not in rec or "diagnoses" not in rec:
            continue
        cid = rec.get("patient_id") or os.path.splitext(os.path.basename(path))[0]
        diags = [str(_val(d.get("name"))) for d in rec.get("diagnoses", []) or []
                 if _val(d.get("name"))]
        hosp = _val(rec.get("encounter", {}).get("hospital", {})) or ""
        cases.append({
            "case_id": cid,
            "path": path,
            "diagnoses": diags,
            "hospital": str(hosp),
        })
    return cases


def evaluate_trigger_signals(record: Dict) -> Dict:
    """规则判定触发信号，返回 {signals, reason, details}。锚定 KR1 字段。"""
    costs = record.get("costs", {}) or {}
    enc = record.get("encounter", {}) or {}

    total = _val(costs.get("total"))
    self_paid = _val(costs.get("self_paid"))
    ins_paid = _val(costs.get("insurance_paid"))
    los = _val(enc.get("length_of_stay_days"))
    procedures = record.get("procedures", []) or []

    def num(x):
        try:
            return float(x) if x is not None else None
        except (TypeError, ValueError):
            return None

    total_n = num(total)
    self_n = num(self_paid)
    ins_n = num(ins_paid)
    los_n = num(los)

    signals = []
    details = {}

    # 1) 自费负担异常：自费金额存在，且自费占比（自费/(自费+医保) 或 自费/总）超阈值
    self_ratio = None
    if total_n and total_n > 0 and self_n is not None:
        self_ratio = self_n / total_n
        if self_ratio >= 0.25:
            signals.append("自费负担异常")
            details["自费负担异常"] = f"自费 {self_n:.0f} 元 / 总费用 {total_n:.0f} 元，自费占比 {self_ratio:.0%}"

    # 2) 高额住院：住院天数或总费用超阈值
    if total_n is not None and total_n >= 5000:
        signals.append("高额住院")
        details["高额住院"] = f"总费用 {total_n:.0f} 元（≥5000 元）"
    elif los_n is not None and los_n >= 7:
        signals.append("高额住院")
        details["高额住院"] = f"住院 {los_n:.0f} 天（≥7 天）"

    # 3) 复查窗口临近：有手术/操作，需要术后复查关怀
    if procedures:
        proc_names = [str(_val(p.get("name"))) for p in procedures if _val(p.get("name"))]
        if proc_names:
            signals.append("复查窗口临近")
            details["复查窗口临近"] = "存在手术/操作：" + "、".join(proc_names[:3])

    # 4) 反复就医：单案无法跨案统计，此处以"住院天数偏长 + 手术"作为持续管理需求的代理信号
    if los_n is not None and los_n >= 3:
        signals.append("持续健康管理")
        details["持续健康管理"] = f"住院 {los_n:.0f} 天，建议出院后持续随访"

    # 无信号命中时的兜底：仍触发"结案关怀"，理由为常规复盘
    if not signals:
        signals.append("常规结案关怀")
        details["常规结案关怀"] = "无高危信号，按常规流程生成就医复盘指引"

    reason = "；".join(details[s] for s in signals)
    return {"signals": signals, "reason": reason, "details": details, "_details": details}


def build_event(case_id: str, record: Dict) -> Dict:
    """构造 claim.closed 触发事件契约（与技术方案文档 6.4 节一致）。"""
    trig = evaluate_trigger_signals(record)
    return {
        "event_type": "claim.closed",
        "case_id": case_id,
        "occurred_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "payload": {
            "claim_status": "closed",
            "patient_id": case_id,
            "medical_materials": ["已结构化病历（模块 a 产出）"],
        },
        "trigger_signals": trig["signals"],
        "trigger_reason": trig["reason"],
        "_details": trig["details"],
    }
