# -*- coding: utf-8 -*-
"""
模块 a · 规则兜底层（正则/规则优先抽取半结构化字段）

plan.txt 要求：用药明细、费用清单多为半结构化表格，用正则/规则优先抽取，LLM 补缺。
这里提供一组纯规则抽取器，返回 (value, confidence, evidence)，既可直接产出结果，
也用于对 LLM 结果做交叉校验（confidence.py）。

所有函数输入是"按阅读顺序排好的 OCR 纯文本"。
"""

from __future__ import annotations

import re
import datetime
from typing import List, Tuple, Optional


# ---------------------------------------------------------------------------
# 日期
# ---------------------------------------------------------------------------

_DATE_PATTERNS = [
    (r'(?P<y>20\d{2})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日', 'ymd_cn'),        # 2025年12月06日
    (r'(?<!\d)(?P<y>20\d{2})[./-](?P<m>\d{1,2})[./-](?P<d>\d{1,2})(?!\d)', 'ymd_num'),  # 2025-12-06 / 2025/12/30
    (r'(?<!\d)(?P<y>20\d{2})(?P<m>\d{2})(?P<d>\d{2})(?!\d)', 'ymd_compact'),  # 20251230（前后不能是数字）
]


def extract_dates(text: str) -> List[Tuple[str, str]]:
    """返回 [(规范化日期 YYYY-MM-DD, 原始串), ...]，按出现顺序。"""
    found: List[Tuple[str, str]] = []
    seen = set()
    for pat, kind in _DATE_PATTERNS:
        for m in re.finditer(pat, text):
            y, mo, d = m.group('y'), m.group('m'), m.group('d')
            try:
                dt = datetime.date(int(y), int(mo), int(d))
            except ValueError:
                continue
            # 过滤明显不合理的年份（病历日期应在 2000~2100 之间）
            if not (2000 <= dt.year <= 2100):
                continue
            norm = dt.isoformat()
            raw = m.group(0)
            if norm not in seen:
                seen.add(norm)
                found.append((norm, raw))
    return found


def parse_date_to_iso(s: str) -> Optional[str]:
    """把任意常见日期串解析成 YYYY-MM-DD。"""
    s = s.strip()
    for pat, kind in _DATE_PATTERNS:
        m = re.search(pat, s)
        if m:
            try:
                return datetime.date(int(m.group('y')), int(m.group('m')), int(m.group('d'))).isoformat()
            except ValueError:
                return None
    return None


def days_between(d1: str, d2: str) -> Optional[int]:
    """出院日 - 入院日 的住院天数（跨天计算）。"""
    a, b = parse_date_to_iso(d1), parse_date_to_iso(d2)
    if not a or not b:
        return None
    da = datetime.date.fromisoformat(a)
    db = datetime.date.fromisoformat(b)
    return (db - da).days


# ---------------------------------------------------------------------------
# 金额
# ---------------------------------------------------------------------------

# 金额数字：逗号千分位（至少一组逗号）或纯整数，可带两位小数
# _MONEY_NUM 产生两个捕获组：group1=整数部分，group2=小数部分（可空）
_INT = r'(?:\d{1,3}(?:,\d{3})+|\d+)'
_MONEY_NUM = r'(' + _INT + r')(?:\.(\d{1,2}))?'
_MONEY_RE = re.compile(r'(?<![\d.])' + _MONEY_NUM + r'\s*元')


def _to_float(m: re.Match, int_grp, dec_grp) -> float:
    int_part = m.group(int_grp).replace(',', '')
    dec = m.group(dec_grp) or '0'
    return float(f"{int_part}.{dec}")


def extract_money(text: str) -> List[float]:
    """提取文本中的所有金额（元）。"""
    out: List[float] = []
    for m in _MONEY_RE.finditer(text):
        out.append(_to_float(m, 1, 2))
    return out


def extract_total_cost(text: str) -> Optional[float]:
    """抽取"总费用/合计/总计"金额。分层：先强关键字（住院总费用/总费用），再弱关键字。"""
    for keys in (
        r'住院总费用|费用总计|总费用',          # 强关键字：无歧义
        r'金额总计|金额合计|医疗费用|总计|合计',  # 弱关键字：可能混入伙食费等，最后兜底
    ):
        pat = re.compile(r'(?:' + keys + r')\s*[:：]?\s*' + _MONEY_NUM)
        m = pat.search(text)
        if m:
            return _to_float(m, 1, 2)
    # 兜底：最大的金额
    ms = extract_money(text)
    return max(ms) if ms else None


# ---------------------------------------------------------------------------
# 带标签的日期（入院/出院/就诊时间）
# ---------------------------------------------------------------------------

_LABELED_DATE_RE = re.compile(
    r'(?P<label>入院日期|入院时间|出院日期|出院时间|就诊时间|记录日期|住院日期)'
    r'\s*[:：]?\s*(?P<date>20\d{2}年\d{1,2}月\d{1,2}日|20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})'
)


def extract_labeled_dates(text: str) -> List[Tuple[str, str]]:
    """抽取带标签的日期，如 入院日期：2025年12月03日 -> [('入院日期','2025-12-03')]。"""
    out = []
    for m in _LABELED_DATE_RE.finditer(text):
        iso = parse_date_to_iso(m.group('date'))
        if iso:
            out.append((m.group('label'), iso))
    return out


def extract_admission_discharge(text: str) -> Tuple[Optional[str], Optional[str]]:
    """从标签抽取 (入院日期, 出院日期)。"""
    labels = extract_labeled_dates(text)
    adm = next((d for l, d in labels if l.startswith("入院")), None)
    dis = next((d for l, d in labels if l.startswith("出院")), None)
    return adm, dis


# ---------------------------------------------------------------------------
# 医保分类汇总（甲/乙/丙/统筹/自费/自付）
# ---------------------------------------------------------------------------

_CLASS_RE = re.compile(
    r'(?P<cls>甲类|乙类|丙类|乙限类|统筹|自费|自付|分段|限价|未对照|个账支付|医保账户支付|基金合计|实收现金)'
    r'\s*[:：]?\s*(?P<amt>' + _INT + r')(?:\.(?P<dec>\d{1,2}))?\s*元?'
)


def extract_insurance_class_summary(text: str) -> List[dict]:
    """抽取「甲类:6330.76元 / 乙类:18892.09元 / 丙类:5967.14元 / 统筹:0 / 自费:0」这类汇总。"""
    out = []
    for m in _CLASS_RE.finditer(text):
        amt = _to_float(m, 'amt', 'dec')
        out.append({"class": m.group('cls'), "amount": amt, "evidence": m.group(0)})
    return out


# ---------------------------------------------------------------------------
# 文档类型识别
# ---------------------------------------------------------------------------

_DOC_TYPE_RULES = [
    ("出院小结", "出院小结"),
    ("出院记录", "出院记录"),
    ("出院诊断书", "出院诊断书"),
    ("24小时内入出院记录", "入出院记录"),
    ("费用汇总清单", "费用清单"),
    ("费用明细汇总清单", "费用清单"),
    ("住院费用结算单", "医保结算单"),
    ("出院结算单", "出院结算单"),
    ("医疗收费明细", "收费明细"),
    ("门诊病历", "门诊病历"),
    ("门诊初诊病历", "门诊病历"),
    ("费用清单", "费用清单"),
    ("出院医嘱", "出院医嘱"),
]


def detect_doc_type(text: str) -> str:
    """根据文本关键词识别文档类型，用于文档路由与溯源。"""
    for kw, t in _DOC_TYPE_RULES:
        if kw in text:
            return t
    return "其它"


# ---------------------------------------------------------------------------
# 用药规格/剂量/频次 基础规则（半结构化明细行）
# ---------------------------------------------------------------------------

_DOSE_SPEC_RE = re.compile(
    r'(?P<dose>\d+(?:\.\d+)?)\s*(?P<unit>mg|g|μg|ug|ml|IU|U|片|粒|袋|支|瓶|包|贴|剂)'
)
_FREQ_RE = re.compile(
    r'(每日[一二三四]次|每[日天][一二三四]次|bid|tid|qd|每日一次|每日二次|每日三次|q8h|q12h|1次/[日天]|每晚一次)'
)
_ROUTE_RE = re.compile(r'(口服|外用|静脉|静滴|静脉注射|肌注|坐浴|吸入|皮下|局部)')


def extract_dose_spec(name_line: str) -> Optional[str]:
    """从一行明细里抽规格/剂量，如 '0.5g/片' '10ml:30mg' '3500IU/0.2ml'。"""
    m = _DOSE_SPEC_RE.search(name_line)
    if m:
        return m.group(0)
    return None


def extract_frequency(text: str) -> Optional[str]:
    m = _FREQ_RE.search(text)
    return m.group(0) if m else None


def extract_route(text: str) -> Optional[str]:
    m = _ROUTE_RE.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# 药品名召回（费用清单/用药明细里的半结构化药品名，规则优先补缺）
# ---------------------------------------------------------------------------

# 只保留"明确的药品剂型"作为后缀；不含"针/片/剂"这类会误伤耗材的泛化后缀
_DRUG_END = (r'(?:注射液|溶液|分散片|缓释片|控释片|肠溶片|含片|胶囊|颗粒|口服液|凝胶|乳膏|软膏'
             r'|栓|滴丸|丸|散|膏|粉针|吸入剂|喷雾剂|气雾剂|滴眼液|糖浆|混悬液|洗剂)')
_DRUG_RE = re.compile(r'([一-龥A-Za-z（）()0-9/.]+?' + _DRUG_END + r')')
# 注射用/吸入用 开头的药（可能无剂型后缀，如 注射用头孢拉定、吸入用七氟烷），保留前缀
_PREP_DRUG_RE = re.compile(r'((?:注射用|吸入用)[一-龥]{2,})')
_NOISE_TOKENS = ['用明细专田音', '宽田明细专用音', '蒙田明细专用音', '实用明细专用章',
                 '费用明细专用章', '费用清单', '专用章', '学第', '明细', '专田音']
# 耗材/器械关键词：命中则排除（非药品）
_DEVICE_KW = ['一次性', '留置', '采血', '注射器', '输液', '三通', '敷料', '敷贴', '喉镜',
              '引流', '吸痰', '吸氧', '导尿', '缝合', '缝线', '回路', '刀头', '螺钉',
              '钢板', '支具', '滑垫', '冲洗器', '导管', '管路', '插管', '传感器',
              '水凝胶', '润滑', '耦合', '针管', '带针', '配针', '侧孔针', '斜面针',
              '采血管', '标本', '蜡块', '干式片', '纱布', '尿袋', '造口', '胶带',
              '绷带', '夹板', '雾化', '面罩', '鼻导管', '呼吸机', '监护', '电极',
              '贴片', '垫片', '皮肤创面', '伤口敷料', '透明敷料', '血样采集']


def _clean_drug(raw: str) -> str:
    s = raw
    for n in _NOISE_TOKENS:
        s = s.replace(n, '')
    s = re.sub(r'^[\d\s.\-—·:：%A-Za-z]+', '', s)   # 去前导数字/剂量/英文
    s = re.sub(r'^\d+(?:\.\d+)?', '', s)
    s = re.sub(r'[（(][^）)]*[）)]$', '', s)          # 去尾部括号（规格/商品名）
    s = s.strip()
    return s


def _is_device(s: str) -> bool:
    return any(k in s for k in _DEVICE_KW)


def extract_medication_names(text: str) -> List[str]:
    """从文本里召回药品名（剂型后缀 + 注射用/吸入用开头）。去重、保持顺序、剔除耗材。"""
    found: List[str] = []
    seen = set()
    for m in _DRUG_RE.finditer(text):
        s = _clean_drug(m.group(1))
        if 2 <= len(s) <= 24 and s not in seen and not _is_device(s):
            seen.add(s)
            found.append(s)
    for m in _PREP_DRUG_RE.finditer(text):
        s = _clean_drug(m.group(1))
        if 2 <= len(s) <= 24 and s not in seen and not _is_device(s):
            seen.add(s)
            found.append(s)
    return found


# ---------------------------------------------------------------------------
# 身份信息（用于脱敏检查）
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r'\b\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx]\b')
_PHONE_RE = re.compile(r'1[3-9]\d{9}')


def find_pii(text: str) -> dict:
    """找身份证号/手机号（用于合规脱敏检查）。"""
    return {
        "id_numbers": _ID_RE.findall(text),
        "phones": _PHONE_RE.findall(text),
    }


def mask_pii(text: str) -> str:
    """对身份证号、手机号做掩码（合规：展示前二次脱敏）。"""
    t = _PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[7:], text)
    t = _ID_RE.sub(lambda m: m.group(0)[:6] + "********" + m.group(0)[-4:], t)
    return t
