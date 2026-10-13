# 模块 b · 多维痛点识别

> 命题能力 b 的实现。输入模块 a 的结构化 JSON，识别患者在就医过程中的痛点，
> 输出：痛点标签 + 严重程度(高/中/低) + 证据溯源 + 可解释理由。
> 硬指标：覆盖临床/费用/体验/理赔体验四类、≥5 类具体痛点、与专家标注一致率 ≥ 85%。

## 1. 痛点分类体系（13 类，覆盖四大类）

| 大类 | type | 名称 | 识别方式 |
|---|---|---|---|
| 临床 | clinical_medication_mismatch | 用药与诊断不匹配 | LLM |
| 临床 | clinical_over_examination | 非必要检查/过度检查 | 规则触发 + LLM |
| 临床 | clinical_over_treatment | 非必要手术/过度治疗 | LLM |
| 临床 | clinical_polypharmacy | 重复用药/用药种类过多 | 规则 |
| 费用 | cost_self_pay_high | 自费比例异常 | 规则 |
| 费用 | cost_drug_ratio_high | 药品费用占比过高 | 规则 |
| 体验 | exp_jargon | 诊断/医嘱术语晦涩 | 规则 |
| 体验 | exp_lack_guidance | 病历缺乏康复/健康指导 | 规则 |
| 体验 | exp_lack_followup | 随访/复诊安排缺失 | 规则 |
| 理赔体验 | claim_pay_advance_need | 垫付需求 | 规则 |
| 理赔体验 | claim_deductible_opaque | 免赔额/报销口径不透明 | 规则 |
| 理赔体验 | claim_material_burden | 材料补件负担 | 规则 |
| 理赔体验 | claim_progress_anxiety | 理赔进度焦虑 | 规则 |

> 理赔体验类为 2026-10 新增，源自对 1.2 万条真实客服对话的洞察，聚焦"保险体验痛点"
> （垫付/免赔额/材料/进度），对应命题"临床/费用/体验"里那个"体验"。

## 2. 识别方法：规则打底 + LLM 语义补足（对应红线）

- **规则引擎**（`rules.py`）负责确定性痛点：自费占比>阈值、药品占比、同类重复用药、
  术语词典、指导/随访缺失。这些"费用/体验/重复用药"类用规则即可稳定判定。
- **LLM 语义判断**（`detect.py`）只负责需医学语义的**临床类**：用药与诊断是否匹配、
  检查/手术是否必要。scope 收敛到临床类，避免 LLM 重复判断规则能做的事（否则误报多）。
- **融合**（`detect.merge`）：按 type 去重，合并证据，取更高严重度。

## 3. 输出 Schema

```json
{
  "patient_id": "CL0300004730956793",
  "pain_points": [
    {"type": "cost_self_pay_high", "name": "自费比例异常", "category": "费用",
     "severity": "中", "score": 0.477,
     "reason": "自费 3273.30 元 / 总费用 6860.63 元 = 47.7%",
     "evidence": [{"source": "costs.self_paid", "snippet": "自费 3273.30 / 总 6860.63"}],
     "method": "rule"}
  ]
}
```

`evidence.source` 定位到模块 a 的字段路径，`snippet` 是原文/数值片段（供溯源与二次脱敏）。

## 4. 评估（专家标注一致率）

`evaluate.evaluate(gold, pred)`：
- **标签 F1**：按 type 集合匹配（兼顾漏报/误报）。
- **严重程度一致率**：共同 type 的 severity 是否一致。

实测（3 病例 vs 专家标注）：**标签 F1 = 1.000，严重程度一致率 = 0.889**（见 `output/效果验证报告_b.md`）。

## 5. 使用

```bash
# 对单个病例（输入是模块 a 的结果 JSON）
python -m modules.b.run output/CL0300004715289963.json

# 纯规则（不调 LLM）
python -m modules.b.run output/CL0300004715289963.json --no-llm

# 批量跑 3 病例 + 评估
python _work/run_b_eval.py
```

输出：`output/<patient_id>.pain_points.json`。
