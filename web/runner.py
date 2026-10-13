# -*- coding: utf-8 -*-
"""
网页后端适配：把上传的图片字节落盘到临时目录，依次调用 modules/a/b/c，
返回结构化结果 + 痛点 + 温度指引。LLM 失败时自动退回规则模式。
"""
import os
import json
import tempfile
import datetime
from typing import List, Dict, Any, Callable

from modules.a.run import run as run_a
from modules.b.run import run as run_b
from modules.c.run import run as run_c


def _write_uploads(uploaded_files, tmp_dir: str) -> None:
    for i, f in enumerate(uploaded_files):
        name = os.path.basename(f.name) or f"img_{i}.jpg"
        with open(os.path.join(tmp_dir, f"{i:03d}_{name}"), "wb") as fh:
            fh.write(f.getbuffer())


def run_pipeline(uploaded_files, pid: str = "",
                 progress: Callable[[str], None] = lambda msg: None,
                 use_llm: bool = True) -> Dict[str, Any]:
    """上传图片 → 全链路（a→b→c），返回 {pid, record, pain_points, guidance}。"""
    img_dir = tempfile.mkdtemp(prefix="img_")
    out_dir = tempfile.mkdtemp(prefix="out_")
    pid = (pid or "").strip() or ("case_" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"))

    _write_uploads(uploaded_files, img_dir)

    # —— 模块 a：结构化 ——
    progress("① 病历结构化（OCR + LLM 抽取）…")
    try:
        if use_llm:
            run_a(img_dir, out_dir, backend="claude", patient_id=pid)
        else:
            run_a(img_dir, out_dir, backend="rules", patient_id=pid)
    except Exception as e:
        progress(f"⚠ LLM 抽取失败，退回纯规则模式：{type(e).__name__}: {str(e)[:120]}")
        run_a(img_dir, out_dir, backend="rules", patient_id=pid)

    rec_path = os.path.join(out_dir, f"{pid}.json")
    record = json.load(open(rec_path, encoding="utf-8"))

    # 持久化到项目 output/，供"品质画像"跨病历聚合纳入本病例
    import shutil
    proj_out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
    os.makedirs(proj_out, exist_ok=True)
    shutil.copy(rec_path, os.path.join(proj_out, f"{pid}.json"))

    # —— 模块 b：痛点 ——
    progress("② 就医痛点识别…")
    try:
        run_b(rec_path, use_llm=use_llm)
    except Exception as e:
        progress(f"⚠ 痛点识别异常，继续：{type(e).__name__}: {str(e)[:120]}")
    pp_path = os.path.join(out_dir, f"{pid}.pain_points.json")
    pain_points = json.load(open(pp_path, encoding="utf-8")) if os.path.exists(pp_path) else {"pain_points": []}

    # —— 模块 c：温度指引 ——
    progress("③ 温度指引生成…")
    try:
        run_c(rec_path, blind_eval=False)
    except Exception as e:
        progress(f"⚠ 指引生成失败：{type(e).__name__}: {str(e)[:120]}")
    g_path = os.path.join(out_dir, f"{pid}.guidance.json")
    if os.path.exists(g_path):
        guidance = json.load(open(g_path, encoding="utf-8"))
    else:
        guidance = _fallback_guidance(pid, record)

    return {"pid": pid, "record": record, "pain_points": pain_points, "guidance": guidance}


def run_from_record(record_path: str,
                    progress: Callable[[str], None] = lambda msg: None,
                    use_llm: bool = True) -> Dict[str, Any]:
    """从已结构化的 record JSON 直接跑 b→c（跳过 OCR），供「理赔结案事件」触发复用。

    返回 {pid, record, pain_points, guidance}，与 run_pipeline 结构一致。
    """
    record = json.load(open(record_path, encoding="utf-8"))
    if "encounter" not in record or "diagnoses" not in record:
        raise ValueError(f"{record_path} 不是结构化病历（缺 encounter/diagnoses 字段）")
    pid = record.get("patient_id") or os.path.splitext(os.path.basename(record_path))[0]
    out_dir = os.path.dirname(os.path.abspath(record_path))

    # —— 模块 b：痛点 ——
    progress("② 就医痛点识别…")
    try:
        run_b(record_path, use_llm=use_llm)
    except Exception as e:
        progress(f"⚠ 痛点识别异常，继续：{type(e).__name__}: {str(e)[:120]}")
    pp_path = os.path.join(out_dir, f"{pid}.pain_points.json")
    pain_points = json.load(open(pp_path, encoding="utf-8")) if os.path.exists(pp_path) else {"pain_points": []}

    # —— 模块 c：温度指引 ——
    progress("③ 温度指引生成…")
    try:
        run_c(record_path, blind_eval=False)
    except Exception as e:
        progress(f"⚠ 指引生成失败：{type(e).__name__}: {str(e)[:120]}")
    g_path = os.path.join(out_dir, f"{pid}.guidance.json")
    guidance = json.load(open(g_path, encoding="utf-8")) if os.path.exists(g_path) else _fallback_guidance(pid, record)

    return {"pid": pid, "record": record, "pain_points": pain_points, "guidance": guidance}


def _fallback_guidance(pid: str, record: Dict) -> Dict:
    """指引生成失败时的兜底（仍尽量个性化）。"""
    def v(f):
        return f["value"] if isinstance(f, dict) and "value" in f else f

    diagnoses = "、".join(v(d.get("name", {})) for d in record.get("diagnoses", []) or [])
    hosp = v(record.get("encounter", {}).get("hospital", {})) or "医院"
    total = v(record.get("costs", {}).get("total", {}))
    lines = [
        "您好，以下是本次就医的简要整理（详细指引暂未生成，请稍后重试）。",
        f"医院：{hosp}；诊断：{diagnoses or '—'}；总费用：{total if total is not None else '—'} 元。",
        "康复与复查请遵医嘱，出现不适及时就医。",
    ]
    return {
        "patient_id": pid,
        "title": "给您的就医整理",
        "greeting": "您好，这次就医辛苦了。",
        "sections": [{"element": "treatment_review", "title": "本次就医回顾",
                      "content": "\n".join(lines)}],
        "cited_facts": [], "full_text": "\n".join(lines),
    }
