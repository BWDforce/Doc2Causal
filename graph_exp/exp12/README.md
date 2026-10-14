# exp12 — discovery vs lang5_evo（gen2 语料，4+1 增量，n=3）

在全新语料（gen2，生成难度修正版）上比较两条管线：

| 管线 | 入口 | 说明 |
|---|---|---|
| discovery（基线） | `multi_extract/run_multi_discovery.py` | 原工程 RAG 发现，无增量模式 |
| **lang5_evo（最新方法）** | `multi_extract/lang5_evo/run_extract.py` | lang5 + 语义合并规则 + 消环 |

语料与金标准：

```
data/Test-text/input/goldgen_gen2/{water,win95pts,alarm}/   # 每主题 5 篇（≤400 词）
gold_gen_text/output_gen2/<dag>/seed1/{manifest.json, extract_theme.txt, alias_dict.json}
```

## 两个新组件（lang5_evo 相对 lang5）

1. **语义合并规则**（默认开启，`--legacy-merge` 回退旧行为，供消融）：
   - 禁止链式合并：节点只能并给"簇代表"，不再通过中间节点串联；
   - 数字/时间戳保护：除数字外内容相同的名字（12:15 vs 12:30）永不合并；
   - 合并后清理退化边，统计写入 `merge_stats.json`。
2. **消环**（默认开启，`--keep-cycles` 关闭，供消融）：
   - 剩余的有向环按"证据最弱边"逐一删除（置信度 + 正负号/机理缺标注、小环优先）；
   - 每次删除留档 `decycle.json`，最终图恒为 DAG。

## 超参数（本次选定值）

- `temperature=0.3`（低温稳定采样）
- `max_steps=2`、`extraction_passes=1`（沿用 exp10_3 以来配置）
- `propagate_threshold=0.2`（沿用 exp10_2 扫描结论）
- `merge_threshold=0.7`（值不动，改规则——exp11 解剖结论）

## 4+1 增量设计

- **phase0**：对每主题文档 01–04 提取；
- **phase1**：
  - lang5_evo：以 phase0 的图为起点（`--init-graph`）只读第 05 篇，真正增量续建；
  - discovery：基线没有续建能力，phase1 用 **5 篇全量重跑**兜底——同一套规范边差量指标对两条管线都计算，并在总结里注明口径差异。

## 运行

```cmd
cd /d "D:\causal extract\causal-cartographer"
set "DEEPSEEK_API_KEY=你的key"

python graph_exp\exp12\run_comparison.py --n 3                       :: 全量（3语料全跑）
python graph_exp\exp12\run_comparison.py --n 3 --corpus water        :: 单语料
python graph_exp\exp12\run_comparison.py --n 1 --corpus water        :: 先快速验证接线
python graph_exp\exp12\run_comparison.py --corpus water --skip-discovery  :: 只跑我方
python graph_exp\exp12\run_comparison.py --evaluate-only             :: 只复盘已有结果
```

## 产物

```
graph_exp/exp12/results/
  <corpus>/<pipeline>/phase0|phase1/run_01..03/
    causal_graph_*.gml + png + run.log
    conflicts.json  decycle.json  merge_stats.json   （lang5_evo 特有）
  summary.json  summary.md
```

## 判读口径

- 边 P/R/F1 以 **全量金边** 为分母；phase0 额外给出"文档 01–04 已植入金边"的horizon 召回；
- `spurious_hit = X/4（phase0）或 X/5（phase1）`：gen2 的程序化诱饵命中数（诱饵已排除金边反向与路径相关对）；
- Δ(0→1)：phase0→phase1 的规范节点/边差量（新增/删除/反转），回答"加第 5 篇对既有结论的影响"。
