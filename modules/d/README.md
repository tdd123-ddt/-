# 模块 d · 品质画像与可解释推荐

> 命题能力 d 的实现。跨病历聚合出 (机构 × 病种) 品质画像，并做**可解释推荐**。
> 硬指标：画像维度 ≥5、推荐 ≥2 条可量化理由、覆盖 ≥3 病种 × ≥5 机构。

## 1. 画像维度（7 维，纯数据驱动）

| 维度 | 含义 | 方向 | 来源字段 |
|---|---|---|---|
| cost_efficiency | 费用效率（均次费用） | 越低越好 | costs.total 均值 |
| length_of_stay | 平均住院天数 | 越低越好 | encounter.length_of_stay_days 均值 |
| self_pay_ratio | 自费比例 | 越低越好 | costs.self_paid / costs.total |
| medication_reasonableness | 用药合理性（重复用药率） | 越高越好 | 1 - 重复用药病例占比 |
| treatment_standard | 治疗规范性 | 越高越好 | 治疗规范标记 |
| readmission_rate | 再入院率（30天） | 越低越好 | 30天再入院标记 |
| satisfaction | 患者满意度 | 越高越好 | 满意度评分 |

**原则**：每个维度都由聚合统计得出、可溯源（`DimensionValue.source`），
不靠 LLM 自由发挥（对应红线"品质画像脱离数据"）。

## 2. 可解释推荐

`recommend.recommend_all`：按病种对机构做 min-max 归一化打分排序，
每条推荐附 ≥2 条**可量化理由**，如：
> 费用效率 7035.62 元，比该病种均值 8321.46 元低 15%；
> 自费比例 44.85%，比该病种均值 50.40% 低 11%。

每条理由带 `evidence`（dimension + value + vs_mean），可溯源到画像字段。

## 3. 数据覆盖

真实数据只有 3 例（3 病种 × 1 机构）。按 plan.txt 风险应对，`demo_data.py` 用
**确定性合成数据补齐机构覆盖**（每条带 `synthetic=True`，可识别；真实 3 例折叠其中），
得到 3 病种 × 5 机构 × 10 例 = 150 条记录。接入真实多机构数据后替换即可。

## 4. 使用

```bash
python -m modules.d.run                # 画像 + 推荐
python -m modules.d.run --top-k 3      # 指定推荐数
```

输出：`output/quality_profiles.json`（画像+推荐）、`output/quality_profiles.md`（可读表）。

## 5. 评估

`evaluate.evaluate`：画像维度数 ≥5、覆盖 ≥3 病种 × ≥5 机构、每条推荐 ≥2 条理由。
实测全部达标（见 `output/效果验证报告_d.md`）。
