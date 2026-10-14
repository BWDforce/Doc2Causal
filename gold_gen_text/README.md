# gold_gen_text — 由 gold_DAG 经 LLM 生成多文档语料

**不用固定模板**，调用大模型 API，把已知 DAG 按"主题→文档风格"展开成自然语言文档。

## API

- 模型：`glm-5.2-fast-preview`
- base URL：`https://llmapi.zihangwu.com.cn:8443/v1`
- key：独立环境变量 **`GLM_API_KEY`**（不与 DeepSeek 的 `DEEPSEEK_API_KEY` 混用）

## 主题驱动

风格**不是命令行参数**，由 DAG 主题自动决定；同一主题产出多种风格的文档（每篇一种风格）：

| DAG | 主题 | extract_theme | 风格 |
|---|---|---|---|
| `water` | 工业水处理厂 | industrial water treatment plant process | manual / test_report / fault_report / maintenance_log |
| `win95pts` | 打印机故障诊断 | printer hardware and software troubleshooting | manual / fault_report / maintenance_log / test_report |
| `alarm` | 呼吸机与监测告警 | ventilator machine and patient monitoring alarm system | manual / test_report / fault_report / maintenance_log |
| 其它 | 工业设备系统（默认） | industrial equipment system operation and failure | manual / test_report / fault_report / maintenance_log |

## 设计要点

1. 拓扑保持的链拆分（贪心最长路径边覆盖，`verify_cover` 校验并集==原边集）。
2. 防名字泄题：LLM 用同义短语改写变量名，`aliases` 返回 surface→canonical。
3. 受控噪声：`--fake-per-doc` 假因果句、`--irrelevant-ratio` 填充句。
4. 跨文档冗余：`--cross-doc-ratio` 部分边在第二篇复述。
5. 主题作为已知信息：manifest 与 `extract_theme.txt` 记录 `theme.extract_theme`，提取时传给 `lang4 --theme`。

## 运行

```cmd
cd /d "D:\causal extract\causal-cartographer"
set "GLM_API_KEY=你的key"

python gold_gen_text\generate_documents.py --dag water    --n-docs 4 --seed 1
python gold_gen_text\generate_documents.py --dag win95pts --n-docs 4 --seed 1
python gold_gen_text\generate_documents.py --dag alarm    --n-docs 4 --seed 1

:: 先不调 LLM 预览
python gold_gen_text\generate_documents.py --dag water --dry-run
```

## 产物路径

**输入文档（统一放在 data\Test-text\input 下）**：

```
data\Test-text\input\goldgen_water_seed1\
  01_manual.json  02_test_report.json  03_fault_report.json  04_maintenance_log.json
```

**元数据（manifest / 提示词 / 主题）**：

```
gold_gen_text\output\water\seed1\
  prompts\doc_*.txt
  manifest.json      （含 input_dir、theme、金边/链/植入边/假边/别名）
  extract_theme.txt
```

## 提取侧配合

```cmd
set /p THEME=<gold_gen_text\output\water\seed1\extract_theme.txt
python multi_extract\lang4\run_extract.py --input-dir data\Test-text\input\goldgen_water_seed1 --output-dir ... --theme "%THEME%"
```

---

## 第二版语料（gen2）——生成难度修正版

针对第一版语料在提取实验中暴露的"人为增加的难度"，第二版生成做了四处修正：

1. **诱饵由程序选定**（不再是模型自由发挥）：排除"金边的反向"与"两变量之间存在任何因果路径"的配对，杜绝"把真话反过来当假话"的陷阱；每个主题 5 篇 × 1 条诱饵；
2. **封闭词表**（closed vocabulary）：先花一次调用为全部变量生成 3-4 条表面短语（存 `alias_dict.json`），文档只能逐字使用表内表达——同一概念不会再用别的说法，评测时别名对齐零缺口；
3. **≤400 词硬上限**：正文超长时自动压缩重写一次；
4. **请求附带上下文窗口参数**：`context_window=1048576`（可用 `--context-window` 调整）。

运行（模型与 API 与第一版相同，`GLM_API_KEY`）：

```cmd
cd /d "D:\causal extract\causal-cartographer"
set "GLM_API_KEY=你的key"

python gold_gen_text\generate_documents.py --dag water    --n-docs 5 --seed 1 --input-parent "data\Test-text\input\goldgen_gen2" --subdir "{dag}" --out-dir "gold_gen_text\output_gen2" --max-words 400 --context-window 1048576
python gold_gen_text\generate_documents.py --dag win95pts --n-docs 5 --seed 1 --input-parent "data\Test-text\input\goldgen_gen2" --subdir "{dag}" --out-dir "gold_gen_text\output_gen2" --max-words 400 --context-window 1048576
python gold_gen_text\generate_documents.py --dag alarm    --n-docs 5 --seed 1 --input-parent "data\Test-text\input\goldgen_gen2" --subdir "{dag}" --out-dir "gold_gen_text\output_gen2" --max-words 400 --context-window 1048576
```

产物：

```
data\Test-text\input\goldgen_gen2\{water|win95pts|alarm}\
  01_manual.json 02_... 03_... 04_... 05_...   (5 篇；第一轮实验用 01-04，05 留给增量实验)

gold_gen_text\output_gen2\{water|win95pts|alarm}\seed1\
  manifest.json  extract_theme.txt  alias_dict.json  prompts\  generate.log
```

> 若接口不识别 `context_window` 字段会返回 4xx，`generate.log` 里能看到原始报错，届时按接口文档改字段名即可。

> 附：完成额度 `--max-tokens` 缺省 **32768**；若返回 `finish_reason=length`（推理占满额度），会自动翻倍重试（直至接近上下文窗口），无需手动调大。
