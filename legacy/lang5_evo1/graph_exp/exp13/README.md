# exp13 — discovery vs lang5_evo（gen5 语料，4+1 增量，n=5）

在 **gen5 语料**（更丰富同义变体 + 矛盾型诱饵）上比较两条管线，每语料重复 **5 次**。

| 管线 | 入口 |
|---|---|
| discovery（基线） | `multi_extract/run_multi_discovery.py`（无增量模式，phase1 为 5 篇全量重跑） |
| **lang5_evo（最新）** | `multi_extract/lang5_evo/run_extract.py`（语义合并规则 + 消环 + LangExtract 重试） |

语料与金标准（gen5）：

```
data/Test-text/input/goldgen_gen5/{water,win95pts,alarm}/
gold_gen_text/output_gen5/<dag>/seed1/{manifest.json, extract_theme.txt, alias_dict.json}
```

## 与 exp12 的不同

1. **n=5**（默认），三语料分开下达（见下）；每条管线 phase0/phase1 各 5 次重复；
2. **API 稳定性守卫**：
   - 每个 phase 的失败自动重试 2 次（`--retries`，间隔 10 秒，重试日志追加进 run.log）；
   - **断点续跑**：已有结果的 run 目录（存在 GML）自动跳过，中途崩溃重跑同一命令即可接续，不重复烧钱；需要全量重算时加 `--force`；
   - 单 phase 超时（`--timeout`，默认 3600 秒）不中断整批；
3. **矛盾型诱饵评测**：新增 `contra` 三指标——每条方向矛盾诱饵的**仲裁解决数 / 残留数 / 保留真方向数**（summary 中 `contra r/s` 列展示 resolved/survived 均值）；
4. LLM 与 URL 与 exp12 一致：`deepseek-v4.1-flash` + `https://llmapi.zihangwu.com.cn:8443/v1`（可 `--model` 覆盖）。

## 运行（三个主题三条指令，依次执行即可）

```cmd
cd /d "<repo-root>"
set "DEEPSEEK_API_KEY=你的DeepSeek官方key"

python graph_exp\exp13\run_comparison.py --corpus water
python graph_exp\exp13\run_comparison.py --corpus win95pts
python graph_exp\exp13\run_comparison.py --corpus alarm
```

失败/中断后重发同一条指令即可自动续跑；审计已有成果：

```cmd
python graph_exp\exp13\run_comparison.py --evaluate-only
```

## 产物

```
graph_exp/exp13/results/<corpus>/<pipeline>/phase0|phase1/run_01..05/
  causal_graph_*.gml + png + run.log
  conflicts.json  decycle.json  merge_stats.json   （lang5_evo 特有）
summary.json  summary.md
```

## 判读要点

- 边 P/R/F1 对全量金边；phase0 另给 01–04 植入金边的 horizon 召回；
- **矛盾型诱饵**（docs 2–5 各 1 条）：真方向先陈、假方向后陈；看 `contra resolved/survived` 判断冲突仲裁是否检出；
- Δ(0→1)：phase0→phase1 规范边差量；
- 每 phase 5 次重复 → summary 给出均值±标准差。
