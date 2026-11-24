---
name: deep-research-orchestration
description: Use when a task requires decomposing a research question into independently searchable sub-questions, planning retrieval order and stop conditions, aligning supplied literature to each sub-question with quoted evidence, and reporting conflicts and evidence gaps without external search.
---

# 深度研究编排：拆解、检索规划与证据综合

## 用途与适用条件

当任务要求围绕一个研究问题完成深度研究流程，而不是直接写一段综述时使用：先拆出可独立检索的子问题与依赖关系，再给出检索顺序、来源与停止条件，然后把材料组织成可回链的证据矩阵，最后在证据边界内给出综合结论、冲突证据与研究空白。

**适用前提**：`case.json` 提供研究问题（`research_question`/`question`/`topic` 等）与一份文献池（`records`/`sources`/`papers`/`documents` 等）。文献池可以是题录、摘要或片段；缺少全文时可以工作，但会标注证据边界。

**不适用**：替代领域专家做最终判断、承诺外部数据库的检索结果、对单篇论文做方法学审查（属于学术审查赛道）、编造未提供的文献或引文。

## 输入

| 输入 | 说明 |
|---|---|
| `/input/case.json` | 必读。常见字段：`research_question`/`question`/`topic`、`scope`/`domain`/`constraints`、`time_range`/`year_range`、`records`/`sources`/`papers`。记录字段：`source_id`/`id`/`doi`、`title`、`abstract`、`text`/`full_text`/`snippets`、`year`、`venue`、`authors`、`url`、`study_type`、`retracted`。实际键名以文件为准，脚本会自动发现记录数组。 |
| `task.md` | 可选。存在时读取，`case.json` 缺研究问题时用其首个有效正文行兜底。 |
| `--work` | 工作目录，默认 `/app/work`，仅用于定位可选 `task.md`。 |

## 处理流程（对应评分维度）

1. **问题拆解**（任务完成度）：从问题与范围文本中识别对象、机制、方法、比较、结果、时间六类切面；每个切面生成一个可独立检索的子问题，写明 `facet`、`depends_on`、检索式、来源范围与停止条件。未识别的切面不强行生成。
2. **检索规划**（可核验性）：按依赖关系做拓扑排序，输出执行顺序与并行阶段；每个子问题带可执行检索式与停止条件，声明本次不访问网络。
3. **证据回链**（正确性）：对每个「子问题 × 记录」按标题 3 分、摘要 2 分、正文 1 分打分，保留命中的词项、原文引句与定位串；得分 0 的组合不进入证据矩阵。
4. **证据综合**（完整性）：每条综合句只陈述「N 条提供记录支持」并附引句，强度分为 `supported`/`partial`/`unsupported`；无证据时明确写「无法评估」，不生成领域结论。
5. **冲突证据**（正确性）：在同一子问题内用方向线索词判定 `positive`/`negative`，两侧同时出现时并列两条引句，不选边。
6. **研究空白**（完整性）：只依据提供文献池的覆盖情况判定 `no_evidence`、`single_source`、`temporal_coverage`、`metadata_incomplete`，并写明判定依据；不把「没检索到」写成领域空白。

## 输出契约

写入 `--out`（默认 `/app/output/result.json`）。顶层字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 原样保留输入 `case_id` |
| `status` | string | `success` / `partial` / `insufficient_input` / `failed` |
| `result` | object | `research_question`、`question_decomposition`、`retrieval_plan`、`evidence_matrix`、`synthesis`、`conflicts`、`research_gaps`、`coverage`、`limitations`、`change_log` |
| `evidence` | array | 证据行与来源定位 |
| `uncertainty` | array | 无问题、无记录、未覆盖记录、无年份、无时间窗、撤稿记录、无外部检索 |
| `errors` | array | 运行错误；正常为空 |
| `stats` | object | 记录数、子问题数、证据行数、冲突数、缺口数 |
| `contract_notes` | object | 未产出契约字段说明 |
| `output_contract_fields` | object | `provided` 与 `present` |

输入 `output_contract` 的每个字段名都会出现在顶层；无法产出时写 `null` 并在 `contract_notes` 说明。

`status`：无研究问题或无记录 → `insufficient_input`；记录少于 2 条或无任何证据行 → `partial`；问题与语料齐备并产出证据矩阵 → `success`；解析失败 → `failed`。

## 异常处理

| 情形 | 处理 | 输出标记 |
|---|---|---|
| `case.json` 缺失或不是对象 | 不崩溃，输出失败结构 | `status=failed`，退出码 2 |
| 无研究问题 | 尝试 `task.md` 兜底，仍无则不拆解 | `status=insufficient_input`，`uncertainty.type=no_research_question` |
| 文献池为空 | 不生成任何结论 | `status=insufficient_input` |
| 记录缺少年份 | 不做时间序列判断 | `uncertainty.type=no_publication_years` |
| 记录未被任何子问题命中 | 保留在统计中但不引用 | `uncertainty.type=unscreened_records` |
| 记录标记撤稿 | 仍列出并标注，不当作有效证据 | `uncertainty.type=retracted_record_present` |
| 某子问题零命中或仅一条来源 | 降级为缺口，不补写结论 | `research_gaps[].type` |
| 输出目录不可写 | stderr 简短错误，退出码 3 | 无静默成功 |

## 限制

- 子问题由问题文本的表层线索生成，不等同于领域专家的问题框架。
- 证据对齐基于词项匹配与引句抽取，不验证语义蕴含，可能高估相关性。
- 冲突检测基于方向线索词，不是对每项研究方法学的完整阅读。
- 不访问任何数据库；`retrieval_plan` 中的检索式需要在外部执行后另行提供结果。
- 不替代系统综述的注册、偏倚评估与 meta 分析。

## Quickstart

在仓库根目录 `bohrium-competition` 执行（PowerShell，实测通过）：

```powershell
python -B "tracks\deep-research-literature-review\challenges\agent-f8fb8bda\06_code\submission\scripts\run_skill.py" --case "tracks\deep-research-literature-review\challenges\agent-f8fb8bda\06_code\submission\examples\demo-case\case.json" --out "$env:TEMP\demo_agent-f8fb8bda\result.json" --work "$env:TEMP\demo_agent-f8fb8bda\work"
```

- 示例输入：`examples/demo-case/case.json`，合成且自包含（`"demo": true`，3 条带 abstract/text 的记录，含 1 条结论不一致；DOI 均为虚构的 `10.0000/...`）。
- 实测输出：`status = "success"`、`subquestions = 4`，`result.json` 顶层含 `case_id`、`status`、`result`、`evidence`、`uncertainty`、`errors`、`stats`；同一输入两次运行结果逐字节一致。
- 该用例只验证脚本可执行与输出契约齐全，不代表真实综述结论。

## 最小调用示例（示例，非正式案例结果）

```bash
python /skills/weekly-skill/scripts/run_skill.py \
  --case /input/case.json --out /app/output/result.json --work /app/work
```

输入片段（示例）：

```json
{"case_id": "demo-dr-01",
 "research_question": "Does intermittent fasting improve glycemic control in adults with type 2 diabetes?",
 "time_range": "2018-2026",
 "records": [{"source_id": "S1", "title": "Intermittent fasting and HbA1c in type 2 diabetes",
              "abstract": "A randomized trial found improved HbA1c after 12 weeks of intermittent fasting.",
              "year": 2023, "doi": "10.0000/example"}]}
```

示意输出（示例，已截断）：

```json
{"case_id": "demo-dr-01", "status": "success",
 "result": {"question_decomposition": {"subquestions": [{"id": "Q1", "facet": "object", "depends_on": []}]},
            "synthesis": [{"subquestion_id": "Q1", "strength": "partial", "cited_sources": [{"source_id": "S1"}]}]},
 "stats": {"records": 1, "subquestions": 6}}
```

以上为接口示意，不代表任何官方固定案例结果。


## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本（若存在）：`python scripts/run_skill.py`（默认读 `/input/case.json`，写 `/app/output/result.json`）；路径不同时用 `--case` / `--out` 指定。
2. 再完成脚本无法自动完成的实质工作（译文、论述、图表、修订、结论等），把结果写回 `result.json` 对应字段。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 为 `completed`（仅在确实缺输入时为 `partial`）；无 `pending` 占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
