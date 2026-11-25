# 异常识别与处置

原则：无论输入如何，都写出结构化 `result.json` 并正常结束；不使用静默成功，不伪造结论。

## 输入与解析

| 情形 | 识别方式 | 处置 | 输出标记 | 退出码 |
|---|---|---|---|---|
| `case.json` 缺失 / 不可读 | `load_json` 抛 `OSError` | 不崩溃，写失败结构 | `status=failed`，`errors[].stage=build` | 0（入口兜底） |
| `case.json` 不是 JSON 对象 | 根节点类型检查 | 同上 | `status=failed` | 0 |
| JSON 语法损坏 | `json.JSONDecodeError` | 同上 | `status=failed`，`errors[].type=JSONDecodeError` | 0 |
| 研究问题缺失 | `build_question` 返回空 | 尝试 `task.md` 兜底，仍无则不拆解 | `status=insufficient_input`，`uncertainty.type=no_research_question` | 0 |
| 文献池为空 | `find_records` 返回空 | 不生成任何结论 | `status=insufficient_input`，`uncertainty.type=no_records` | 0 |
| 记录键名不同（`studies`/`abstracts`/`library`/`entries` 等） | `RECORD_KEYS` + 嵌套遍历 | 自动发现记录数组 | 正常输出 | 0 |
| 记录缺年份 | `parse_year` 返回 `None` | 不做时间序列判断 | `uncertainty.type=no_publication_years` | 0 |
| 记录无摘要、仅有标题 | `abstract` 为空 | 标题兜底，`support_level` 记 `partial` | `research_gaps[].type=metadata_incomplete` | 0 |
| 记录标记撤稿 | `retracted` 为真 | 排除出证据矩阵，保留在筛选台账 | `uncertainty.type=retracted_record_present`；`screening[].decision=exclude` | 0 |
| DOI 或标题重复 | `find_duplicates` | 保留首条，重复条目标 `duplicate_of` | `uncertainty.type=duplicate_records_removed`；`prisma.n_duplicates` | 0 |
| 记录未被任何子问题命中 | 证据行为空 | 保留统计，不引用，标 `pending` | `uncertainty.type=unscreened_records` | 0 |

## 结论与自检

| 情形 | 处置 | 输出标记 |
|---|---|---|
| 某子问题零命中 | 不补写结论，转 `research_gaps` | `type=no_evidence`，附 `queries_tried` 与 `human_confirm` |
| 某子问题仅一条来源 | 降级为缺口 | `type=single_source` |
| 冲突双方引文或 locator 校验失败 | 门禁失败 | `gates[].gate=conflict_validity`，`status` 降为 `partial` |
| 引文无法在原文回证 | 门禁失败 | `verification.quote_check.not_found` 非空 |
| 契约键缺失 | 门禁失败 | `verification.contract_check.missing`，`status` 降为 `partial` |
| PRISMA 计数不平衡 | 门禁失败 | `prisma.balance_ok=false` |
| 迭代与声明计划不一致 | 门禁失败 | `gates[].gate=iteration_plan_alignment` |
| 出现占位词 | 门禁失败 | `gates[].gate=no_placeholder` |

门禁整体结论写入 `validator_verdict`：`pass` / `fail`，并列出 `failed_gates`。任何门禁失败都不阻断交付，只降低 `status` 并在 `uncertainty` 说明。

## 输出与文件系统

| 情形 | 处置 | 退出码 |
|---|---|---|
| 输出目录不存在 | 自动 `mkdir -p` | 0 |
| 输出目录不可写 | stderr 返回简短 JSON 错误，不静默成功 | 3 |
| 配套产物写入失败 | 记录到 `errors[]`，`result.artifacts=[]`，主结果照常写出 | 0 |
| 未捕获异常 | 入口兜底写出最小 `result.json`（`status=partial`） | 0 |

## 边界说明

- 本 Skill 不访问网络，因此“未检索到”只表示“已提供语料中没有”，不代表领域空白；该区别写在每个 gap 的 `distinction` 字段。
- 启发式判断（切面识别、方向判定、冲突配对）均在 `limitations` 与相关字段标注，不冒充领域专家结论。
