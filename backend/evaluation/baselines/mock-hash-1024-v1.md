# RAG Mock 种子基线

## 汇总指标

| 指标 | 结果 |
| --- | ---: |
| Embedding 模型 | mock-hash-1024 |
| 相似度阈值 | 0.2 |
| Top-K | 3 |
| 样例总数 | 14 |
| 可回答样例 | 10 |
| 拒答样例 | 4 |
| Recall@3 | 100.00% |
| MRR | 90.00% |
| 拒答准确率 | 100.00% |
| 引用有效率 | 不适用 |
| 平均检索延迟 | 24.64 ms |
| P95 检索延迟 | 176.84 ms |

## 逐题明细

| case_id | Recall | RR | 拒答正确 | 引用有效 | 延迟 |
| --- | ---: | ---: | :---: | :---: | ---: |
| project-identity-owner | 100.00% | 100.00% | 是 | 不适用 | 176.84 ms |
| project-first-scope | 100.00% | 100.00% | 是 | 不适用 | 14.95 ms |
| meeting-schedule | 100.00% | 50.00% | 是 | 不适用 | 12.09 ms |
| meeting-goal | 100.00% | 50.00% | 是 | 不适用 | 11.87 ms |
| raw-file-storage | 100.00% | 100.00% | 是 | 不适用 | 13.68 ms |
| vector-metadata-storage | 100.00% | 100.00% | 是 | 不适用 | 20.34 ms |
| redis-responsibility | 100.00% | 100.00% | 是 | 不适用 | 11.21 ms |
| supported-formats-ocr | 100.00% | 100.00% | 是 | 不适用 | 12.16 ms |
| no-evidence-policy | 100.00% | 100.00% | 是 | 不适用 | 12.78 ms |
| redis-failure-recovery | 100.00% | 100.00% | 是 | 不适用 | 12.85 ms |
| unknown-bronze-casting | 不适用 | 不适用 | 是 | 不适用 | 12.31 ms |
| unknown-octopus-neurons | 不适用 | 不适用 | 是 | 不适用 | 11.53 ms |
| unknown-rainforest-rainfall | 不适用 | 不适用 | 是 | 不适用 | 11.24 ms |
| unknown-orbital-fuel | 不适用 | 不适用 | 是 | 不适用 | 11.16 ms |

## 解释边界

- 该基线用于验证本地 Mock 词面哈希检索的工程链路，不代表真实语义检索质量。
- `meeting-schedule` 和 `meeting-goal` 的正确证据排在第 2 名，因此整体 MRR 为 90%。
- 首题包含数据库连接和模型初始化冷启动，延迟不能直接与另一台机器比较。
- 本轮没有运行回答模型，因此引用有效率为“不适用”。
