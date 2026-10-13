# -*- coding: utf-8 -*-
"""
模块 a · 官方命题材料 JSON → StructuredRecord 桥接器

用途：把比赛官方下发的「命题材料提取结果」JSON（materialType + fields 结构化键值）
转换成模块 a 的 StructuredRecord（gold 金标准）。

官方 JSON 已是结构化提取结果（相当于官方 OCR + 结构化抽取的产物），
字段可直接映射，无需重跑 LLM/OCR。本模块负责：
  1. 遍历一个病例目录下的所有材料 JSON；
  2. 汇总同一字段（诊断/费用/日期/患者信息…），做去重与归一化；
  3. 产出 StructuredRecord（gold），供 evaluate.py 对照你的 pipeline pred 算 F1。

用法：
    from modules.a.adapt import case_to_record, build_gold_set
    rec = case_to_record('D:/KR1_补充材料/命题材料提取结果/CL0300004435037004')
    gold_map = build_gold_set('D:/KR1_补充材料/命题材料提取结果')  # {case_id: StructuredRecord}
"""

from __future__ import annotations

import os
import json
import re
from datetime import datetime
from typing import Dict, List, Any, Optional

from .schema import (StructuredRecord, Patient, Encounter, Diagnosis, Procedure,
                     Medication, Cost, CostItem, Field, Evidence, DocumentInfo,
                     LabFinding, TimelineEvent, Discharge)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _F(value, method="official", conf=0.95, source="", snippet=""):
    """把官方字段包装成 Field。官方提取视为高置信标注。"""
    if _is_empty(value):
        value = None
    ev = [Evidence(source=source, snippet=snippet)] if value not in (None, "", []) else []
    return Field(value=value, confidence=conf, evidence=ev,
                 missing=(value in (None, "", [])), method=method)


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("元", "").replace("￥", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _days_between(adm: str, dis: str) -> Optional[int]:
    """入院/出院日期 → 住院天数（含当天）。"""
    a, b = _parse_date(adm), _parse_date(dis)
    if a and b and b >= a:
        return (b - a).days + 1
    return None


def _parse_date(s: Any) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(str(s).strip()[:10], fmt)
        except ValueError:
            continue
    m = re.search(r'(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})', str(s))
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


# ---------------------------------------------------------------------------
# 材料解析
# ---------------------------------------------------------------------------

def _load_case_materials(case_dir: str) -> List[Dict[str, Any]]:
    """读一个病例目录下所有 JSON，返回 [{materialType, fields, originalFilename}, ...]。"""
    mats = []
    for f in sorted(os.listdir(case_dir)):
        if not f.lower().endswith(".json"):
            continue
        p = os.path.join(case_dir, f)
        try:
            with open(p, encoding="utf-8") as fh:
                j = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(j, dict) or not j.get("fields"):
            continue
        mats.append({
            "materialType": j.get("materialType", "其他"),
            "fields": j.get("fields", {}),
            "filename": j.get("originalFilename") or f,
        })
    return mats


def _is_empty(v: Any) -> bool:
    """判断字段值是否为空（兼容官方 OCR 把无值写成字面量 'None'/'null'/'NA' 的情况）。"""
    if v is None:
        return True
    if isinstance(v, str) and v.strip().lower() in ("none", "null", "na", "n/a", "无", ""):
        return True
    if v == "" or v == []:
        return True
    return False


def _first(mats: List[dict], *keys: str, prefer_types: tuple = ()) -> Any:
    """在所有材料里找第一个非空的字段值。prefer_types 优先在这些 materialType 里找。"""
    if prefer_types:
        for mt in prefer_types:
            for m in mats:
                if m["materialType"] == mt:
                    for k in keys:
                        v = m["fields"].get(k)
                        if not _is_empty(v):
                            return v, m["materialType"], m["filename"]
    for m in mats:
        for k in keys:
            v = m["fields"].get(k)
            if not _is_empty(v):
                return v, m["materialType"], m["filename"]
    return None, None, None


def _collect(mats: List[dict], key: str) -> List[tuple]:
    """收集某字段在所有材料中的值（去重保序），返回 [(value, materialType, filename)]。"""
    out, seen = [], set()
    for m in mats:
        v = m["fields"].get(key)
        if _is_empty(v):
            continue
        # 列表值展开
        vals = v if isinstance(v, list) else [v]
        for it in vals:
            s = str(it).strip()
            if s and s not in seen:
                seen.add(s)
                out.append((it, m["materialType"], m["filename"]))
    return out


def _cost_blocks(mats: List[dict]) -> List[dict]:
    """汇总所有费用清单的 costDetailList 明细行。"""
    rows = []
    for m in mats:
        for blk in m["fields"].get("blocks", []) or []:
            for r in blk.get("costDetailList", []) or []:
                rows.append(r)
    return rows


# ---------------------------------------------------------------------------
# 映射主函数
# ---------------------------------------------------------------------------

def case_to_record(case_dir: str, patient_id: Optional[str] = None) -> StructuredRecord:
    mats = _load_case_materials(case_dir)
    pid = patient_id or os.path.basename(os.path.abspath(case_dir))
    rec = StructuredRecord(patient_id=pid)

    # ---- 患者 ----
    gender, gmt, gfn = _first(mats, "gender", "sex")
    age, amt, afn = _first(mats, "age")
    name, nmt, nfn = _first(mats, "patientName", "name")
    rec.patient.gender = _F(gender, source=gfn or "")
    rec.patient.age = _F(age, source=afn or "")
    rec.patient.name = _F(name, source=nfn or "")
    rec.patient.patient_id = _F(_first(mats, "hospitalNo")[0])

    # ---- 就诊 ----
    hosp, hmt, hfn = _first(mats, "hospitalName", "hospital")
    dept, dmt, dfn = _first(mats, "department", "ward")
    doc, omt, ofn = _first(mats, "doctorName", "doctor")
    adm, amt2, afn2 = _first(mats, "admissionDate", prefer_types=("出院小结", "入院记录", "病案首页"))
    dis, dmt2, dfn2 = _first(mats, "dischargeDate", prefer_types=("出院小结", "病案首页"))
    rec.encounter.type = _F("住院" if adm else "门诊")
    rec.encounter.hospital = _F(hosp, source=hfn or "")
    rec.encounter.department = _F(dept, source=dfn or "")
    rec.encounter.doctor = _F(doc, source=ofn or "")
    rec.encounter.admission_time = _F(adm, source=afn2 or "")
    rec.encounter.discharge_time = _F(dis, source=dfn2 or "")
    los = _days_between(adm, dis)
    rec.encounter.length_of_stay_days = _F(los)

    # ---- 诊断（出院诊断优先，其次诊断，去重 + 去 ICD 前缀）----
    diags = _collect(mats, "dischargeDiagnosis")
    if not diags:
        diags = _collect(mats, "admissionDiagnosis")
    if not diags:
        diags = _collect(mats, "diagnosis")
    _seen_diag = set()
    for i, (v, mt, fn) in enumerate(diags):
        # 去 ICD 编码前缀（如 "(n84.100)"）与重复
        clean = re.sub(r'^\(?[A-Za-z]?\d+(?:\.\d+)?\)?', '', str(v)).strip()
        if not clean or clean in _seen_diag:
            continue
        _seen_diag.add(clean)
        rec.diagnoses.append(Diagnosis(
            name=_F(clean, source=fn),
            type=_F("出院诊断" if mt == "出院小结" else "诊断"),
            is_primary=(i == 0),
        ))

    # ---- 手术/操作（仅费用清单"手术费"明细中的确定性手术项；不解析自由文本，避免噪声）----
    # 噪声黑名单：非手术的"检查/造影/加收/镇痛/护理/监测"类，与否定表述
    _proc_noise = ("镇痛", "体温保护", "监测", "检查", "造影", "加收", "费", "无",
                   "未", "否", "护理", "导尿", "灌洗", "上药", "物理", "康复", "心理",
                   "标本", "诊断")
    _seen_proc = set()
    for r in _cost_blocks(mats):
        cat = r.get("itemCategoryName") or ""
        name = r.get("detailName") or ""
        if cat != "手术费":
            continue
        s = str(name)
        if not s or not any(k in s for k in ("术", "切除", "穿刺", "切开", "缝合", "移植", "置管", "吻合", "剥离", "成形")):
            continue
        if any(n in s for n in _proc_noise):
            continue
        name = re.sub(r'^\[.*?\]', '', s).strip()
        name = re.sub(r'[（(].*?[)）]', '', name).strip()  # 去括号说明
        name = re.sub(r'费$', '', name).strip()
        if name and name not in _seen_proc and 2 < len(name) < 40:
            _seen_proc.add(name)
            rec.procedures.append(Procedure(name=_F(name, source="费用清单")))

    # ---- 费用 ----
    rows = _cost_blocks(mats)
    total = sum(_num(r.get("feeAmount")) or 0 for r in rows)
    # 医保结算单优先：有支付分解字段时，用结算单的 totalMedicalExpense
    _settle = None
    for m in mats:
        fl = m["fields"]
        if "insurancePayment" in fl or "poolingPaymentTotal" in fl:
            _settle = fl
            break
    if _settle:
        st_total = _num(_settle.get("totalMedicalExpense"))
        if st_total:
            total = st_total
        ins_paid = _num(_settle.get("insurancePayment")) or _num(_settle.get("poolingPaymentTotal"))
        if ins_paid is not None:
            rec.costs.insurance_paid = _F(round(ins_paid, 2), source="医保结算单")
        self_paid = _num(_settle.get("personalCashPayment")) or _num(_settle.get("totalPersonalBurden"))
        if self_paid is not None:
            rec.costs.self_paid = _F(round(self_paid, 2), source="医保结算单")
        # 住院天数/主诊断也可从结算单补（更权威）
        if not rec.encounter.length_of_stay_days.value:
            _ipd = _settle.get("inpatientDays")
            if _ipd is not None:
                rec.encounter.length_of_stay_days = _F(_num(_ipd), source="医保结算单")
        if not rec.diagnoses:
            _pdx = _settle.get("primaryDiagnosis")
            if _pdx:
                rec.diagnoses.append(Diagnosis(name=_F(_pdx, source="医保结算单"),
                                               type=_F("主诊断"), is_primary=True))
    if total:
        rec.costs.total = _F(round(total, 2))
        rec.costs.currency = _F("CNY")
        # 分类汇总
        by_cat: Dict[str, float] = {}
        for r in rows:
            cat = r.get("itemCategoryName") or r.get("itemCategoryCode") or "其他"
            by_cat[cat] = by_cat.get(cat, 0) + (_num(r.get("feeAmount")) or 0)
        for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
            rec.costs.breakdown.append(CostItem(
                category=_F(cat), amount=_F(round(amt, 2))))

    # ---- 用药（从费用清单的药品明细行映射，清洗到"纯药名"粒度）----
    _drug_cats = ("西药费", "中成药费", "中药饮片费", "西药", "中成药", "中草药")
    _seen_drugs = set()
    for r in _cost_blocks(mats):
        cat = r.get("itemCategoryName") or ""
        name = r.get("detailName") or ""
        if cat in _drug_cats or ("药" in cat and name):
            s = str(name)
            # 清洗噪声：去 [单列]/[带] 前缀、◆/● 符号、【商品名】、括号说明、规格/数量后缀
            s = re.sub(r'^\[[^\]]*\]', '', s)
            s = re.sub(r'[◆●▲■]', '', s)
            s = re.sub(r'【[^】]*】', '', s)
            s = re.sub(r'[（(][^（）()]*[)）]', '', s)
            # 去规格/数量后缀：如 "100ug*1支"、"0.9%0.45g/50ml"、"co100片"、"5mg*1瓶"、"100ml*2瓶"
            s = re.sub(r'\d+(?:\.\d+)?(?:mg|g|ug|μg|ml|iu|u|片|支|瓶|袋|粒|丸|包|盒|单位)', '', s, flags=re.I)
            s = re.sub(r'[*/×xX]\s*\d+\s*(?:支|瓶|袋|粒|丸|包|盒|片)?', '', s)
            s = re.sub(r'\d+(?:\.\d+)?%', '', s)  # 去浓度 "0.9%" 前的数字（保留比例本身无意义，直接去）
            s = re.sub(r'[#＃*]+$', '', s)
            s = re.sub(r'\s+', '', s)
            s = s.strip()
            if s and s not in _seen_drugs and 2 < len(s) < 40:
                _seen_drugs.add(s)
                rec.medications.append(Medication(
                    name=_F(s, source="费用清单"),
                    category=_F(cat)))

    # ---- 检查/检验（费用清单"检查费/检验费/化验费"明细项，与 pred 同源可对齐）----
    # pred 侧 LLM 从费用清单明细抽取检查项目名，gold 也同源，保证口径一致。
    _seen_labs = set()
    for r in _cost_blocks(mats):
        cat = r.get("itemCategoryName") or ""
        name = r.get("detailName") or ""
        if cat not in ("检查费", "检验费", "化验费", "治疗费", "诊查费"):
            continue
        s = str(name)
        if not s:
            continue
        # 清洗：去 [单列] 前缀、括号说明、（#）后缀
        s = re.sub(r'^\[[^\]]*\]', '', s)
        s = re.sub(r'[（(][^（）()]*[)）]', '', s)
        s = re.sub(r'[#＃*]+$', '', s)
        s = s.strip()
        if s and s not in _seen_labs and 2 < len(s) < 40:
            _seen_labs.add(s)
            rec.labs_imaging.append(LabFinding(
                name=_F(s, source="费用清单"), result=_F(None)))

    # ---- 文档元信息 ----
    for m in mats:
        rec.documents.append(DocumentInfo(
            file=m["filename"],
            doc_type=_F(m["materialType"]),
        ))

    # ---- 出院医嘱 ----
    dis_inst, _, _ = _first(mats, "discharge_status", "instructions")
    rec.discharge.instructions = _F(dis_inst)

    return rec


def build_gold_set(base_dir: str) -> Dict[str, StructuredRecord]:
    """遍历 base_dir 下所有病例目录，产出 {case_id: StructuredRecord}。"""
    gold = {}
    for c in sorted(os.listdir(base_dir)):
        d = os.path.join(base_dir, c)
        if os.path.isdir(d):
            try:
                gold[c] = case_to_record(d)
            except Exception as e:
                print(f"[warn] {c} 解析失败: {e}")
    return gold


if __name__ == "__main__":
    import sys
    base = sys.argv[1] if len(sys.argv) > 1 else r"D:/KR1_补充材料/命题材料提取结果"
    gold = build_gold_set(base)
    print(f"成功解析 {len(gold)} 个病例")
    # 抽样打印一个
    k = list(gold.keys())[0]
    print("\n示例病例", k, "：")
    r = gold[k]
    print("  诊断:", [d.name.value for d in r.diagnoses])
    print("  入院:", r.encounter.admission_time.value, "出院:", r.encounter.discharge_time.value,
          "住院天数:", r.encounter.length_of_stay_days.value)
    print("  总费用:", r.costs.total.value, "费用分类数:", len(r.costs.breakdown))
    print("  医院:", r.encounter.hospital.value, "性别:", r.patient.gender.value, "年龄:", r.patient.age.value)
