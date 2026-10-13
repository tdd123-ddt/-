# -*- coding: utf-8 -*-
"""
模块 e · 真实问答知识库（RAG 底座）

从 1.2 万条增值服务客服会话（电话/在线/企微）里抽取「客户问 → 客服答」的问答对，
清洗、去重、建轻量检索索引（关键词 + 主题），作为"追问回答"和"预判式 FAQ"的真实依据，
杜绝纯靠 LLM 胡编报销/理赔口径。

数据源：D:/20260917-0923_增值服务电话在线企微会话文本.xlsx
  列：session_id / session_time / 服务类型 / oneid / account_id / 企微id / 会话原文
  会话原文格式：每行以「客户：」「客服：」「机器人：」开头（\n 分隔）。

产物：
  output/qa/qa_pairs.jsonl    清洗后的问答对（question, answer, service_type, topic, keywords）
  output/qa/qa_index.json     主题 → 问答对 id 列表 的倒排索引
"""

from __future__ import annotations

import os
import re
import json
from typing import List, Dict, Tuple, Optional

DATA_XLSX = "D:/20260917-0923_增值服务电话在线企微会话文本.xlsx"
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "output", "qa")


# ---------------------------------------------------------------------------
# 主题分类（用于检索和预判式 FAQ 的映射）
# ---------------------------------------------------------------------------

# 主题 key -> (名称, 触发关键词)
TOPICS = {
    "理赔材料": ["材料", "发票", "诊断证明", "出院小结", "费用清单", "病历", "票据", "清单", "要什么", "需要哪些", "准备什么"],
    "报销比例": ["报销", "报销比例", "能报多少", "报销多少", "赔付比例", "赔多少"],
    "垫付": ["垫付", "先行赔付", "先赔", "预付", "打款"],
    "免赔额": ["免赔", "起付", "门槛"],
    "等待期": ["等待期", "生效", "观察期"],
    "理赔进度": ["进度", "到哪", "多久", "什么时候", "审核", "赔款", "到账"],
    "复查复诊": ["复查", "复诊", "随访", "多久复查"],
    "增值服务": ["绿通", "质子", "特药", "陪诊", "护工", "体检", "疫苗", "健康卡", "救援", "代驾", "家政"],
    "退保": ["退保", "退费", "退款", "取消"],
    "续保": ["续保", "续费", "保费"],
}

# 无意义的客服口水话（不当作"有效答案"）
FILLER = {"嗯", "好的", "好的好的", "请稍等", "稍等", "我看一下", "收到", "在的", "您好", "你好",
          "请问还有什么可以帮您", "还有其他问题吗", "感谢来电", "祝您生活愉快", "再见",
          "邀请您评价我们的服务", "您的反馈是我们进步的方向", "请问怎么称呼您", "您贵姓",
          "对方未接听", "请保持电话畅通", "会第一时间回复您", "之后优先为您处理"}

# 答案里若含这些"非实质"短语，视为口水话（即使长度够）
FILLER_SUBSTR = ["现在休息中", "非工作时间", "已下班", "下班啦", "您可以先留言", "上班后", "第一时间回复",
                 "请留意查收", "会电话联系", "感谢您的耐心", "祝您", "再见", "评价", "反馈", "稍后联系"]


def _segments(text: str) -> List[Tuple[str, str]]:
    """把会话原文切成 [(角色, 内容)]，角色 ∈ {客户, 客服, 机器人}。"""
    out: List[Tuple[str, str]] = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(客户|客服|机器人)[：:]\s*(.*)$", line)
        if m:
            out.append((m.group(1), m.group(2).strip()))
        # 无角色前缀的行，归到上一个说话人（客服/客户续说）
        elif out:
            role, prev = out[-1]
            out[-1] = (role, prev + " " + line)
    return out


def _is_question(s: str) -> bool:
    """判断一句话是不是"问题"（带问号或疑问词）。"""
    s = s.strip()
    if not s:
        return False
    if "?" in s or "？" in s:
        return True
    if any(w in s for w in ("多少", "怎么", "如何", "吗", "呢", "有没有", "哪些", "什么", "哪里", "几点", "几时")):
        return True
    return False


def _is_filler(s: str) -> bool:
    """判断客服回答是否为口水话（非实质内容）。"""
    if s in FILLER:
        return True
    if any(x in s for x in FILLER_SUBSTR):
        return True
    return False


def _clean(s: str) -> str:
    """清洗一句话：去表情、去链接、去多余空白。"""
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]", "", s)  # emoji
    s = re.sub(r"[\r\t ]+", " ", s).strip()
    return s


def _topic_of(text: str) -> str:
    """按关键词命中主题，返回主题 key；命中多个取最先（优先级按 TOPICS 顺序）。"""
    for key, kws in TOPICS.items():
        if any(kw in text for kw in kws):
            return key
    return "其他"


def _has_keyword(text: str) -> bool:
    """是否含任何业务关键词（过滤纯寒暄会话）。"""
    return any(kw in text for kws in TOPICS.values() for kw in kws)


def extract_pairs(text: str, service_type: str) -> List[Dict]:
    """从一条会话里抽「客户问 → 客服答」的问答对。

    规则：客户连续发问后，紧接着的客服非口水话回答即为答案；
    若客户问句本身是"客服转述客户问题"（企微里客服常复述），也归为问题。
    """
    segs = _segments(text)
    pairs: List[Dict] = []
    i = 0
    n = len(segs)
    while i < n:
        role, content = segs[i]
        if role != "客户":
            i += 1
            continue
        q = _clean(content)
        # 客户这条必须是"问句"（带问号或疑问词），避免陈述句/寒暄误入
        if _is_question(q):
            # 找后续客服的有效回答（跳过多条口水话）
            j = i + 1
            answer_parts: List[str] = []
            while j < n and j < i + 6:
                r2, c2 = segs[j]
                if r2 == "客服":
                    c2c = _clean(c2)
                    if c2c and not _is_filler(c2c) and len(c2c) >= 4:
                        answer_parts.append(c2c)
                    # 客服已给出实质回答后，遇到客户新发言就停
                    if j + 1 < n and segs[j + 1][0] == "客户":
                        break
                elif r2 == "客户":
                    break
                j += 1
            if answer_parts:
                answer = "；".join(answer_parts)[:300]
                topic = _topic_of(q + " " + answer)
                pairs.append({
                    "question": q[:200],
                    "answer": answer,
                    "service_type": service_type,
                    "topic": topic,
                })
        i += 1
    return pairs


def build(output_dir: str = OUT_DIR, limit: int = 0) -> Tuple[str, str]:
    """全量抽取，去重，写 qa_pairs.jsonl + qa_index.json。返回两文件路径。"""
    import openpyxl

    os.makedirs(output_dir, exist_ok=True)

    wb = openpyxl.load_workbook(DATA_XLSX, read_only=True)
    ws = wb["明细"]

    pairs: List[Dict] = []
    seen: set = set()
    n_rows = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        service_type = row[2]
        text = row[6]
        if not text:
            continue
        n_rows += 1
        if limit and n_rows > limit:
            break
        for p in extract_pairs(text, service_type):
            key = (p["question"], p["answer"])
            if key in seen:
                continue
            seen.add(key)
            pairs.append(p)

    # 写 jsonl
    pairs_path = os.path.join(output_dir, "qa_pairs.jsonl")
    with open(pairs_path, "w", encoding="utf-8") as f:
        for i, p in enumerate(pairs):
            p["id"] = i
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    # 建倒排索引：topic -> [id]
    index: Dict[str, List[int]] = {}
    for p in pairs:
        index.setdefault(p["topic"], []).append(p["id"])

    index_path = os.path.join(output_dir, "qa_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    # 统计
    stats = {k: len(v) for k, v in index.items()}
    print(f"抽取完成：{n_rows} 条会话 → {len(pairs)} 个问答对")
    print("主题分布：", json.dumps(stats, ensure_ascii=False))
    return pairs_path, index_path


if __name__ == "__main__":
    build()
