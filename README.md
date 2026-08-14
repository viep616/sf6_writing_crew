# SF6 写作智能体 Crew

基于 CrewAI 1.15.10 的三智能体协作写作系统：读取 QE（Quantum ESPRESSO）仿真结果，
自动撰写论证本项目纳米传感材料优于对比材料的中文科技论述。

## 智能体架构

```
data/qe_results.md（队友填写的仿真摘要）
        │
        ▼
 [data_analyst]      仿真数据分析员 → 物理量对照分析
        │
        ▼
 [argument_builder]  论证策略师     → 数据→机制→性能 论点框架
        │
        ▼
 [tech_writer]       科技写作员     → output/论证报告.md
```

三个 Agent 均使用通义千问 qwen-plus（crewai 原生 dashscope provider，国内站端点）。

## 快速开始

```powershell
# 1. 配置密钥（首次）
Copy-Item .env.example .env
# 编辑 .env，填入千问 API Key（DASHSCOPE_API_KEY）

# 2. 填写仿真数据
#    编辑 data/qe_results.md，按模板填入 QE 结果（留空的项 Agent 会跳过）

# 3. 运行（在本项目目录下，复用上级目录的 .venv 环境）
& "..\.venv\Scripts\python.exe" run.py
```

运行结束后查看 `output/论证报告.md`。

## 文件说明

| 文件/目录 | 用途 |
|-----------|------|
| `crew.jsonc` | 任务链与 Crew 设置（三任务顺序执行） |
| `agents/data_analyst.jsonc` | 仿真数据分析员配置 |
| `agents/argument_builder.jsonc` | 论证策略师配置 |
| `agents/tech_writer.jsonc` | 科技写作员配置 |
| `data/qe_results.md` | 仿真数据输入模板（队友填写） |
| `.env` | 千问 API 密钥（不入库，从 .env.example 复制） |
| `output/` | 成稿输出目录（不入库） |
| `knowledge/` | 可选：放置项目背景资料供 Agent 检索 |
| `tools/` | 可选：自定义工具（Python） |

## 数据填写注意事项

1. `data/qe_results.md` 第 3 节对照表是论证核心，尽量填全
2. 单位单独放"单位"列，不要混在数值里
3. 第 6 节"队友备注"最关键——把只有仿真人员知道的结论和写作红线写进去
4. 留空的项 Agent 会标注"未提供"，不会编造

## 环境要求

- Python 3.10–3.13
- crewai==1.15.10、crewai-tools==1.15.10（见 pyproject.toml）
- 通义千问 API Key（DashScope OpenAI 兼容模式）
