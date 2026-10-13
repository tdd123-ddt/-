# 模块 a · 病历结构化与医疗事件重建

> 命题能力 a 的实现。目标：把理赔提交的医疗文档（住院病历、出院小结、手术记录、用药明细、
> 费用清单等）结构化为一套**通用 JSON**，含字段级置信度与证据溯源，供模块 b/c/d 使用。
> 硬指标：关键字段 F1 ≥ 0.90，关键字段缺失率 < 5%，输出符合预定义 JSON Schema 且含字段级置信度。

---

## 1. 目录结构

```
modules/a/
├── schema.py        # 通用 JSON Schema（Pydantic 模型 + JSON Schema 导出）
├── schema.json      # 导出的 JSON Schema（内部契约/校验用）
├── flat_schema.py   # LLM 抽取用的"扁平 schema" + 转回 StructuredRecord 的适配层
├── ocr.py           # OCR 层（可插拔，默认 RapidOCR-ONNX，中文好、无 torch）
├── rules.py         # 规则兜底层（日期/金额/医保分类/药品名/文档类型等正则抽取）
├── extract.py       # 抽取层（LLM 文本-JSON 抽取 + 规则补缺 + 离线回放）
├── confidence.py    # 字段级置信度（模型置信 × 规则交叉校验 × OCR 置信）
├── evaluate.py      # F1 / 关键字段缺失率 评估脚本
├── run.py           # 端到端 CLI：图片目录 → OCR → 抽取 → JSON
└── prompts/
    └── system_prompt.md   # 抽取 Prompt（通用，不绑定单一病种/医院）
```

> 设计要点：内部 schema（`schema.py`）用 `Field{value,confidence,evidence}` 包装、含嵌套 `$defs`，
> 对 LLM 的 function-calling 太复杂（DeepSeek 会返回空对象）。因此抽取时让 LLM 填一个
> **扁平、具体、可空**的结构（`flat_schema.py` 的 `FlatRecord`），再由 `coerce_to_record()`
> 包装成带字段级置信度/证据的 `StructuredRecord`。

配套（仓库根目录）：
- `output/<patient_id>.json` —— 结构化结果（演示产出）
- `gold/<patient_id>.json`   —— 权威标注集（评估用 gold）
- `_work/eval_demo.py`       —— 评估演示（规则基线 vs gold）

---

## 2. 通用性设计（如何适配"各种病例"）

本模块**不假设**只有某一类病例，而是用三层机制保证通用：

1. **开放枚举 + 布尔位**：诊断类型用开放式字符串（主/次/入院/出院/初步/中医/西医），
   并用 `is_primary` 标记主诊断；中医与西医诊断并列保留，不写死取值。
2. **就诊类型自适应**：`encounter.type` 覆盖 住院/门诊/急诊/24小时入出院；
   "住院天数、入院/出院时间"等字段仅在住院病例下适用，门诊病例自动标记为"不适用"，
   不计入缺失率（见 `confidence.key_field_status`）。
3. **费用口径归一化**：不同医保体系（吉林的 甲/乙/丙类；浙江的 统筹/自付/自费/补助减免）
   统一映射到 `total / insurance_paid / self_paid / other_paid` 四个口径 + `breakdown` 分类金额 +
   `insurance_class_summary` 医保分类汇总，映射不了的进 `extra`，不丢信息。
4. **用药兼容中西药**：`category` 区分 西药/中成药/中药饮片；中药饮片的"剂量"即"每味克数"。
5. **文档类型路由**：`rules.detect_doc_type` 识别出院记录/出院小结/费用清单/门诊病历/结算单/
   收费明细/检查报告等，新文档类型只需加一条关键词规则。
6. **未知信息兜底**：`extra` 字段统一收纳未覆盖内容；`documents` 记录来源文档清单，
   保证不丢信息、可溯源。

> 关键理念：**schema 是"最大公约数 + 扩展点"**，而不是"模板"。新病种（骨折/肺炎/冠心病/糖尿病…）
> 只是填充诊断/手术/用药内容不同，结构不变；新医院只是费用/医保字段映射不同，靠归一化吸收。

---

## 3. 关键字段与 Schema 摘要

与 plan.txt 对齐的关键字段：

| 关键字段 | schema 位置 | 说明 |
|---|---|---|
| 诊断（主/次/ICD） | `diagnoses[]` | name / type / icd_code / is_primary |
| 手术（名称/术式/日期） | `procedures[]` | name / date / anesthesia / code / body_part |
| 用药（通用名/剂量/疗程） | `medications[]` | name / spec / dose / frequency / route / course / purpose / category |
| 住院天数 | `encounter.length_of_stay_days` | 与"出院-入院"交叉校验 |
| 费用构成（总/医保/自费/分类） | `costs` | total / insurance_paid / self_paid / other_paid / breakdown / insurance_class_summary |

每个叶子字段统一为：
```json
{"value": "...", "confidence": 0.95, "evidence": [{"source": "出院记录", "snippet": "原文片段"}], "missing": false, "method": "llm"}
```
`evidence` 是证据溯源（供模块 b 痛点定位、展示前二次脱敏用）。

---

## 4. 字段级置信度机制

`confidence.recompute_confidence` 在抽取结果上叠加交叉校验：

| 校验 | 规则 | 效果 |
|---|---|---|
| 住院天数 | 与 `出院时间 - 入院时间` 对比 | 一致→×1.0；偏差≤1天→×1.0；冲突→×0.3 |
| 总费用 | 与 `医保支付 + 个人支付 + 补助减免` 求和对比（误差<5%） | 一致→×1.0；否则×0.5 |
| 证据溯源 | 字段有 evidence → 微幅加成；无值 → 标 missing | 提升可信字段置信 |

置信度来源优先级：LLM 自报置信 → 规则交叉校验修正 → OCR 低置信降权。

---

## 5. 评估方法（F1 + 缺失率）

`evaluate.evaluate(gold, pred)`：
- **标量字段**（姓名/性别/年龄/日期/金额…）：归一化后精确匹配（金额容差 0.01）。
- **列表字段**（诊断/手术/用药/检查）：按项目名做集合级 F1（顺序无关，允许漏报/误报）。
- **宏观 F1** = 标量准确率 与 列表 F1 的加权平均。
- **关键字段缺失率** = 缺失(且适用)的关键字段数 / 适用关键字段数（就诊类型自适应）。

运行：
```bash
python _work/eval_demo.py   # 规则基线 vs gold（演示评估框架 + 量化规则/LLM 差距）
```

已知语义字段（诊断/手术/用药名）规则无法可靠抽取，必须 LLM；这正说明 LLM+规则混合的必要性。
目标 F1 ≥ 0.90 以 LLM 抽取结果对标注集评估。

---

## 6. 使用方式

### 6.1 端到端（真实 LLM 抽取）
```bash
# DeepSeek 的 Anthropic 兼容端点（默认）
export ANTHROPIC_API_KEY=sk-...
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export ANTHROPIC_MODEL=deepseek-chat
python -m modules.a.run data/<patient_id> -o output --backend claude

# 原生 Claude（设 ANTHROPIC_MODEL 即可，不设 base_url）
export ANTHROPIC_API_KEY=sk-ant-...
export ANTHROPIC_MODEL=claude-sonnet-5-5
python -m modules.a.run data/<patient_id> -o output --backend claude
```

### 6.2 离线回放（无 API key，用已抽取 JSON）
```bash
python -m modules.a.run data/<patient_id> -o output --backend offline \
    --offline-json gold/<patient_id>.json
```

### 6.3 纯规则基线
```bash
python -m modules.a.run data/<patient_id> -o output_rules --backend rules
```

### 6.4 单文件 OCR
```bash
python modules/a/ocr.py <image.jpg>
```

---

## 7. 如何扩展到新病种 / 新文档类型

1. **新病种**：无需改代码。诊断/手术/用药由 LLM 从文本抽取，schema 结构不变。
2. **新医院格式**：无需改代码。费用口径归一化已覆盖常见医保体系；特殊字段进 `extra`。
3. **新文档类型**：在 `rules.py::_DOC_TYPE_RULES` 加一条 `(关键词, 类型)`；在 `system_prompt.md`
   里无需改动（schema 已覆盖）。若发现高频新字段，在 `schema.py` 扩展对应模型并同步
   `schema.json`，保持向后兼容（新增字段默认缺失）。
4. **换 OCR 后端**：实现 `ocr.BaseOCREngine` 子类，传入 `ocr_image(engine=...)` 即可。

---

## 8. 数据合规（模块 a 视角）

- 只抽取画像与指引所需字段，不存储无关原文（`extra.raw_ocr` 仅在 rules 基线调试时保留，
  生产可关闭）。
- 姓名/身份证/手机号等标识：`rules.mask_pii` 提供掩码；展示前必须二次脱敏。
- 证据 `evidence.snippet` 用于溯源，展示时同样脱敏。
- 审计：`Field.method` 记录抽取来源（llm/rule/ocr），便于追溯。
