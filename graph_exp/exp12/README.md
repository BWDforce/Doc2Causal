# exp12 — discovery vs lang5_evo（gen3 语料，4+1 增量，每语料 n=1）

在 **gen3 语料**（保留适度同义/近义变量表述，生成难度修正版第二代）上比较两条管线。

| 管线 | 入口 | 说明 |
|---|---|---|
| discovery（基线） | `multi_extract/run_multi_discovery.py` | 原工程 RAG 发现，无增量模式 |
| **lang5_evo（最新方法）** | `multi_extract/lang5_evo/run_extract.py` | lang5 + 语义合并规则 + 消环 |

语料与金标准（gen3）：

```
data/Test-text/input/goldgen_gen3/{water,win95pts,alarm}/   # 每主题 5 篇（≤400 词，含适度同义变体）
gold_gen_text/output_gen3/<dag>/seed1/{manifest.json, extract_theme.txt, alias_dict.json}
```

> gen2（完全封闭词表）语料的 water 结果已归档在 `results_gen2/`，本轮实验对比基准是 gen3。

## lang5_evo 语义合并规则（本轮有效版本）

1. 只允许与"簇代表"直接合并（禁链式）；
2. **数字/时间戳保护**：只在数值上不同的名字永不合并；
3. **槽位模式保护（本轮新增）**：两名字是同一词槽模式、只差一个内容词（如 dissolved vs total 同一时刻、inlet vs outlet）永不合并——系统性差异不是同义噪声；
4. 合并后清理退化边；统计写入 `merge_stats.json`（含 `protected_pattern`）。

消环默认开启：剩余有向环逐条删"证据最弱边"并留档 `decycle.json`，输出图恒为 DAG。

## 超参数（选定值，延续 exp12-gen2）

- temperature=0.3、max_steps=2、extraction_passes=1
- propagate_threshold=0.2、merge_threshold=0.7（阈值不变，靠新规则治"同刻不同形态"过度合并）

## 4+1 增量设计（保持不变）

- phase0：文档 01–04 提取；
- phase1：lang5_evo 以 phase0 图为起点（`--init-graph`）只读第 05 篇做增量；discovery 无续建能力，phase1 用 5 篇全量重跑兜底（口径差异在总结中注明）。

## 运行

```cmd
cd /d "D:\causal extract\causal-cartographer"
set "DEEPSEEK_API_KEY=你的key"

python graph_exp\exp12\run_comparison.py                  :: 三个语料各测一次 4+1（默认 n=1）
python graph_exp\exp12\run_comparison.py --corpus water   :: 单语料
python graph_exp\exp12\run_comparison.py --skip-discovery :: 只要我方
python graph_exp\exp12\run_comparison.py --evaluate-only  :: 只复盘
```

## 产物

```
graph_exp/exp12/results/
  <corpus>/<pipeline>/phase0|phase1/run_01/
    causal_graph_*.gml + png + run.log
    conflicts.json  decycle.json  merge_stats.json   （lang5_evo 特有）
  summary.json  summary.md
```

## 判读口径

- 边 P/R/F1 以全量金边为分母；phase0 额外给"01–04 植入金边"的 horizon 召回；
- `spurious_hit = X/4（phase0）或 X/5（phase1）`：程序化诱饵命中（已排除金边反向与路径相关对）；
- Δ(0→1)：phase0→phase1 规范边差量（新增/删除/反转）；
- lang5_evo 的 `merge_stats.json` 关注 `protected_pattern`——它对应 exp12-gen2 中"端点被合并组顶替"造成的隐性漏检修复。
