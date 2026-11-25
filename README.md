# 深度研究编排 Skill（Deep Research Orchestration）

把一份研究问题与一池已提供的文献，加工成可复核的研究交付物：问题拆解、先声明的检索计划、逐轮检索留痕、证据回链、冲突双方引文、研究空白，以及 6 个配套产物文件。纯 Python 标准库实现，不联网、不引入第三方依赖。

## 用途与适用前提

当任务要求“完成一次深度研究流程”而不是“直接写一段综述”时使用：先拆出可独立检索的子问题与依赖关系，声明检索顺序与停止条件，再按计划逐条回放检索、筛选文献、回链证据，最后在证据边界内给出综合结论、冲突与空白。

前提：输入提供研究问题（`research_question` / `question` / `topic` 等）与一份文献池（`records` / `sources` / `papers` / `studies` 等）。文献池可以是题录、摘要或片段；缺少全文仍可工作，但会在 `limitations` 与 `uncertainty` 标注证据边界。

不适用：替代领域专家做最终判断、承诺外部数据库的检索结果、对单篇论文做方法学审查、编造未提供的文献或引文。

## 环境与依赖

- Python 3.10+。
- 仅使用标准库（`argparse`、`json`、`re`、`os`、`sys`、`hashlib`、`csv`、`tempfile`、`pathlib`、`unicodedata`）。
- 见 `requirements.txt`（无第三方包）。

## Quickstart

在包根目录执行（PowerShell，实测通过）：

```powershell
python -B scripts/run_skill.py `
  --case examples/demo-case/case.json `
  --work "$env:TEMP\demo_work" `
  --out "$env:TEMP\demo_out\result.json"
```

参数等价：`--out` 与 `--output` 等价；默认 `--case /input/case.json`、`--out /app/output/result.json`、`--work /app/work`。

运行后，`result.json` 所在目录会同时出现 6 个配套产物（见下）。同一输入连续运行两次，`result.json` 逐字节一致。

## 输入契约

`case.json` 必须是 JSON 对象。常用字段：

| 字段 | 说明 |
|---|---|
| `case_id` | 原样回写到输出 |
| `research_question` / `question` / `topic` / `objective` / `aim` | 研究问题；缺失时回退读取 `task.md` 首个有效正文行 |
| `scope` / `domain` / `constraints` | 研究范围，用于识别切面 |
| `time_range` / `year_range` | 时间窗，用于时间维空白判定 |
| `records` / `sources` / `papers` / `documents` / `literature` / `items` / `corpus` / `evidence_pool` / `hits` / `studies` / `abstracts` / `library` / `entries` | 文献池；也支持在嵌套结构中自动发现记录 |
| `output_contract` | 期望的顶层字段名；脚本会补齐并在 `contract_notes` 说明来源 |

记录字段（别名自动匹配）：`source_id` / `id` / `doi` / `pmid`、`title`、`abstract`、`text` / `full_text` / `snippets`、`year`、`venue` / `journal`、`authors`、`url`、`study_type`、`retracted`。

## 输出契约

主输出 `result.json` 顶层字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 输入 `case_id` 原样回写 |
| `status` | string | `success` / `partial` / `insufficient_input` / `failed` |
| `result` | object | 研究成品：`research_question`、`question_decomposition`、`retrieval_plan`（`state=declared`）、`evidence_matrix`、`synthesis`、`conflicts`、`research_gaps`、`coverage`、`limitations`、`change_log`、`artifacts` |
| `evidence` | array | 证据行与来源条目 |
| `uncertainty` | array | 无问题、无记录、未覆盖记录、缺年份、撤稿、重复、无外部检索等 |
| `errors` | array | 运行错误；正常为空 |
| `screening` | array | 逐条筛选决定：`include` / `exclude` / `pending`，含 `reason`、`criteria_applied`、`duplicate_of` |
| `prisma` | object | `n_identified`、`n_duplicates`、`n_screened`、`n_excluded_by_code`、`n_included`、`n_pending`、`balance_ok` |
| `iterations` | array | 每个子问题一次执行留痕：`status`、`queries_run[]`、`artifacts_ref[]`、`new_evidence_ids[]`、`synthesis_note` |
| `stats` | object | 记录数、子问题数、证据行数、冲突数、缺口数、筛选计数、迭代数 |
| `verification` | object | 引文回证、locator 解析、契约键、冲突双源、gap 依据、迭代对齐 |
| `self_check` | array | 逐项自检 `item` / `passed` / `detail` |
| `gates` | array | 门禁聚合结果 |
| `validator_verdict` | object | `verdict`、`checks_total`、`checks_passed`、`all_checks_passed`、`failed_gates` |
| `contract_notes` | object | 契约字段来源说明 |
| `output_contract_fields` | object | `provided` 与 `present` |

`status` 规则：无研究问题或无记录 → `insufficient_input`；记录少于 2 条或无任何证据行 → `partial`；问题与语料齐备并产出证据矩阵 → `success`；解析失败 → `failed`。若门禁未全部通过，`success` 会降级为 `partial`。

## 配套产物（写入 `--out` 所在目录）

| 文件 | 内容 |
|---|---|
| `research_report.md` | 面向人工阅读的研究报告：计划、PRISMA、逐子问题综合、冲突、空白、限制 |
| `synthesis_matrix.csv` | 每行一条「子问题 × 来源」证据绑定，含 relevance、support_level、polarity、quote |
| `screening_log.csv` | 每行一条文献的筛选决定、理由、所用标准、去重指向 |
| `prisma_flow.svg` | 语料级 PRISMA 计数流程图 |
| `provenance.json` | 记录标题/DOI/引文的 sha256 摘要与 locator 映射 |
| `validation.json` | `verification` / `self_check` / `gates` / `validator_verdict` 的落盘副本 |

每个产物写入后都会执行 `os.path.exists` 自检，并登记到 `result.artifacts`（`name`、`kind`、`path`、`exists`、`bytes`、`sha256`）。

## 处理流程

见 `docs/WORKFLOW.md`（8 步协议与代码位置对照）。方法论规则见 `rules/rules_deep_research.md`。

## 异常处理

见 `docs/ERROR_HANDLING.md`。要点：输入缺失、JSON 损坏、输出目录不存在时仍写出结构化 `result.json` 并以退出码 0 结束；写盘失败时 stderr 返回简短错误并返回退出码 3。

## 文件清单

```text
SKILL.md                         技能说明与调用契约
README.md                        本文件：用途、输入输出、产物、限制
requirements.txt                 依赖声明（仅标准库）
worklog.md                       本轮改动与验证记录
GITHUB_URL.txt                   仓库地址
scripts/run_skill.py             主入口：拆解、计划、筛选、证据、综合、自检
scripts/sk_artifacts.py          配套产物写入器（6 个文件）
rules/rules_deep_research.md     可复用的深度研究规则
docs/WORKFLOW.md                 8 步流程与代码映射
docs/ERROR_HANDLING.md           异常识别与处置
examples/demo-case/case.json     合成、自包含的演示输入
examples/example-result.json     用 demo 输入实际运行得到的 result.json（已标注 demo）
```

## 限制

- 子问题由问题文本的表层线索生成，不等同于领域专家的问题框架。
- 证据对齐基于词项匹配与引句抽取，不验证语义蕴含，可能高估相关性。
- 冲突检测基于方向线索词，不是对每项研究方法学的完整阅读。
- 不访问任何数据库；`iterations[].queries_run` 只回放“对已提供语料”的检索，外部检索未执行。
- 不替代系统综述的注册、偏倚评估与 meta 分析。

## 维护

- 无随机数、无时间戳、无网络访问，输出确定性。
- 修改后请执行：`python -m py_compile scripts/run_skill.py scripts/sk_artifacts.py`，并用同一 demo 连跑两次比较 `result.json` 字节。
