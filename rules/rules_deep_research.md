# 深度研究规则（可复用）

本文件把 Skill 的判定口径写成可被他人套用的规则。规则与 `scripts/run_skill.py` 的实现一一对应；若要复用，只需保持规则编号与输出字段的映射。

## R1 问题拆解

- 从研究问题与范围文本中识别六类切面：对象、机制、方法、比较、结果、时间。
- 每个切面生成一个可独立检索的子问题，写明 `facet`、`depends_on`、检索式、来源范围与停止条件。
- 未识别的切面不强行生成；子问题词项集合重叠达到 75% 时合并，避免重复检索。
- 输出位置：`result.question_decomposition.subquestions[]`。

## R2 检索计划先声明再执行

- 先做拓扑排序，声明 `retrieval_plan.state = "declared"`、执行顺序 `order`、阶段 `stages`、每个子问题的 `queries` 与 `stop_conditions`。
- 声明之后才回放检索；回放记录写入 `iterations[]`，每条含 `status`、`queries_run[]`、`artifacts_ref[]`、`new_evidence_ids[]`、`synthesis_note`。
- `iterations[].queries_run[].query` 必须与 `retrieval_plan.queries[子问题]` 一一对应；不一致即视为计划漂移。
- 本次运行只检索“已提供语料”，外部检索未执行，须在 `uncertainty` 声明。

## R3 筛选与 PRISMA

- 先按 DOI（缺失时按规范化标题）去重；重复记录标 `duplicate_of`，不进入证据矩阵。
- 每条记录给出唯一筛选决定：`include`（命中子问题词项且被引用）、`exclude`（重复、撤稿、无题录正文）、`pending`（保留在语料但未命中）。
- 每条决定附 `reason` 与 `criteria_applied` 列表。
- PRISMA 计数必须自平衡：`n_screened = n_excluded_by_code + n_included + n_pending`，其中 `n_screened = n_identified - n_duplicates`。
- 输出位置：`screening[]`、`prisma`。

## R4 证据回链

- 每条证据必须给出 `source_id`、`locator` 与原文 `quote`；`quote` 必须能在记录的标题/摘要/正文中逐字找到。
- `locator` 必须能在输入 `case.json` 中解析到具体记录。
- 每个 `source_id` 只归入一个子问题，证据矩阵不重复列出同一文献。
- 支持强度：`direct`（摘要/正文结局句且命中词项）、`partial`（标题兜底或分数不足）、`weak`；标题兜底不得计为 `direct`。

## R5 冲突证据

- 仅当两条记录方向相反（positive 对 negative/no_difference）且共享主题词时成对报告。
- 冲突双方各带 `quote` 与 `locator`，且必须来自不同来源（`source_distinct = true`）。
- 同一对文献只出现一次；不选边，双方并列呈现。

## R6 研究空白

- 只依据已提供语料的覆盖情况判定 `no_evidence`、`single_source`、`temporal_coverage`、`metadata_incomplete`。
- 每个 gap 必须给出 `basis`、`bounded_claim`、`queries_tried[]`、`human_confirm`。
- 必须区分“语料无”（`corpus_state = corpus_empty` 或 `corpus_present_without_match`）与“未检索到”（`retrieval_status = external_queries_declared_not_run`）；不得把未执行的外部检索写成领域空白。

## R7 自检与门禁

输出前必须机械复核以下门禁，任一失败则 `status` 不得为 `success`：

1. `case_contract`：`case_id` 一致、`status` 属枚举、契约键齐全。
2. `quote_integrity`：全部引文可回证。
3. `locator_resolvable`：全部 locator 可解析。
4. `conflict_validity`：冲突双方引文非空且不同源。
5. `gap_evidence`：gap 有依据、检索记录与人工确认。
6. `prisma_balance`：筛选计数平衡。
7. `iteration_plan_alignment`：迭代与声明计划一致。
8. `strength_consistency`：`supported` 结论只引用 `direct` 证据。
9. `no_placeholder`：无 `TODO` / `TBD` / `FIXME` 占位。
