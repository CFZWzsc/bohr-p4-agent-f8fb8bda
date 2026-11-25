# Worklog

## 2026-11-25 — 可核验产物密度补齐

### 改了什么

- `scripts/run_skill.py`
  - `RECORD_KEYS` 增加 `studies`、`abstracts`、`library`、`entries`，提高真实案例的记录发现率。
  - 新增 DOI/标题去重（`find_duplicates`）、逐条筛选台账（`build_screening`）与 PRISMA 计数（`build_prisma`）。
  - `retrieval_plan` 增加 `state="declared"` 与 `queries`，新增 `iterations[]` 回放每个子问题的声明查询。
  - 冲突双方补 `quote_field`，并断言 `source_distinct`（不同 locator 且不同 `source_id`）。
  - 研究空白增加 `queries_tried[]`、`human_confirm`、`corpus_state`、`retrieval_status`、`distinction`，区分“语料无”与“未检索到”。
  - 新增 `attach_checks`：产出 `verification`、`self_check`、`gates`、`validator_verdict`；门禁失败时把 `success` 降为 `partial`。
  - 去重与撤稿记录不进入证据矩阵与冲突配对，避免同一文献重复计数。
- 新增 `scripts/sk_artifacts.py`：写 `research_report.md`、`synthesis_matrix.csv`、`screening_log.csv`、`prisma_flow.svg`、`provenance.json`、`validation.json`，逐个 `os.path.exists` 自检后登记到 `result.artifacts`。
- 新增 `README.md`、`docs/WORKFLOW.md`、`docs/ERROR_HANDLING.md`、`rules/rules_deep_research.md`、`requirements.txt`、`examples/example-result.json`。
- `SKILL.md` 只补 Files 清单与产物说明，不扩写方法论。

### 为什么

原包只有 `result.json` 一个产物，没有自检块、没有筛选台账、没有检索执行留痕、没有可复用文档。评分差距集中在“生态贡献”“可核验性”“完整性”。本轮全部补齐均不依赖新算法，也不改变原有证据对齐逻辑，风险低、可复核。

### 验证记录

- `python -m py_compile scripts/run_skill.py scripts/sk_artifacts.py`：通过。
- demo 输入 `examples/demo-case/case.json`：
  - `status=success`，子问题 4，证据行 3，冲突 2，缺口 2；
  - `prisma`：identified 3 / duplicates 0 / screened 3 / excluded 0 / included 3 / pending 0，`balance_ok=true`；
  - `iterations` 4 条（3 条有命中、1 条 gap 审计）；
  - `validator_verdict=pass`，9/9 门禁通过；
  - 6 个配套产物全部生成并登记 `exists=true`。
- 同一 demo 连续运行两次，`result.json` SHA256 一致。
- 异常输入（缺失文件、空记录、重复 DOI、撤稿记录）均写出 `result.json` 且退出码 0。
- 包内无第三方依赖、无令牌、无主机绝对路径、无 `__pycache__`。

### 已知边界

- 冲突与方向仍由词表判定，不是方法学阅读。
- 外部检索未执行；`iterations[].queries_run[].executed=true` 仅表示对已提供语料回放。
- 子问题切面识别依赖英文/中文表层线索，换领域时仍需人工复核拆解树。
