# 工作流：8 步协议与代码映射

本 Skill 强制执行“先声明、再执行、留痕、回证、自检”的顺序。每一步都对应 `result.json` 中的可核验字段，便于逐条复核。

| 步 | 动作 | 代码位置 | 输出字段 |
|---|---|---|---|
| 1 | 读取 `case.json`，发现文献池并规范化记录 | `load_json` / `find_records` / `normalize_record` | `evidence[]` 中的 `kind=source` |
| 2 | 解析研究问题，识别切面，生成子问题与依赖 | `build_question` / `detect_facets` / `parse_question` / `merge_specs` | `result.question_decomposition` |
| 3 | 声明检索计划（顺序、查询、停止条件） | `order_plan` + `build_payload` 中 `retrieval_plan` | `result.retrieval_plan.state="declared"`、`queries`、`order`、`stages` |
| 4 | 去重与筛选，形成 PRISMA 计数 | `find_duplicates` / `build_screening` / `build_prisma` | `screening[]`、`prisma` |
| 5 | 回放每个子问题的查询并留痕 | `build_iterations` | `iterations[]`（`status`、`queries_run[]`、`new_evidence_ids[]`） |
| 6 | 证据回链：打分、抽引句、绑定 locator | `build_evidence` / `prepare_record` / `best_outcome_sentence` | `result.evidence_matrix[]` |
| 7 | 综合结论、冲突双方、研究空白 | `build_synthesis` / `build_conflicts` / `build_gaps` | `result.synthesis`、`result.conflicts`、`result.research_gaps` |
| 8 | 自检门禁，写主结果与配套产物 | `attach_checks` / `sk_artifacts.write_artifacts` | `verification`、`self_check`、`gates`、`validator_verdict`、`result.artifacts` |

## 执行顺序（单次运行）

```text
读入 → 拆解 → 声明计划 → 去重/筛选 → 回放检索 → 证据回链
     → 综合/冲突/空白 → 契约补齐 → 自检门禁 → 写 6 个产物 → 写 result.json
```

- 第 3 步只在内存中“声明”，第 5 步才“执行”；两者通过 `retrieval_plan.queries` 与 `iterations[].queries_run` 对齐。
- 第 8 步若任一门禁失败，`status` 由 `success` 降为 `partial`，并在 `uncertainty` 追加 `self_check_failed`。
- `result.artifacts` 只有在 `os.path.exists` 通过后才登记，避免“文档声明 > 实际文件”。

## 复核命令

```powershell
python -B scripts/run_skill.py --case examples/demo-case/case.json --work "$env:TEMP\w" --out "$env:TEMP\o\result.json"
python -m py_compile scripts/run_skill.py scripts/sk_artifacts.py
```

两次运行后比较 `result.json` 的 SHA256，应完全一致。
