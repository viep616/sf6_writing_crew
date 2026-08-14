# SF6 写作智能体 Crew

基于 CrewAI 1.15.10 的四智能体协作写作系统：读取 QE（Quantum ESPRESSO）仿真结果，
自动撰写论证本项目纳米传感材料优于对比材料的**中文学术论文**，并内置两道防幻觉防线。

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
 [tech_writer]       科技写作员     → output/论文_初稿.md（论文六部分）
        │
        ▼
 [reviewer]          审稿人         → output/论文_终稿.md + 论文_审稿意见.md
        │
        ▼
 防线一（代码级）    tools/validate_report.py 数值白名单校验
                     全过   → 正常时间戳留档
                     未过   → 留档文件名加「_未通过校验」+ output/校验问题.txt
```

四个 Agent 均使用通义千问 qwen-plus（crewai 原生 dashscope provider，国内站端点）。

## 两道防幻觉防线

| 防线 | 位置 | 抓什么 | 机制 |
|------|------|--------|------|
| 防线二（生成时） | reviewer Agent | 语义与措辞：自相矛盾、主语歧义、无据外推、编造参考文献 | 审稿人逐项核查初稿并产出修订终稿 + 审稿意见 |
| 防线一（生成后） | `tools/validate_report.py` | 数字事实：报告中出现数据文件里不存在、也无法推导的数值 | 数据文件全量数值白名单 + 差值/和推导校验 |

**校验规则**：终稿中每个「数值+单位」必须满足其一——
1. 命中白名单（出现在 `data/qe_results.md` 的表格或散文中）；
2. 可由同单位两个白名单数值相减/相加得到（如 −1.85 − (−0.31) = −1.54 eV）；
3. 其余一律报警（默认策略：**留档不阻断**，文件名加 `_未通过校验` 后缀，问题清单见 `output/校验问题.txt`）。

已知边界（由防线二审稿人兜底）：无单位的数字（如"约 6 倍""10⁵"）不在防线一提取范围；
`data/derived_formulas.md` 中声明的公式目前作为提示供人工核对，代码仅内置差值/和推导。

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

运行结束后查看 `output/`：`论文_终稿.md`（成品）、`论文_审稿意见.md`（评审留痕）、
`论文_初稿_时间戳.md`（对比用）、`论文_终稿_时间戳[.md|_未通过校验.md]`（历史留档）。

## 文件说明

| 文件/目录 | 用途 |
|-----------|------|
| `crew.jsonc` | 任务链与 Crew 设置（四任务顺序执行） |
| `agents/data_analyst.jsonc` | 仿真数据分析员配置 |
| `agents/argument_builder.jsonc` | 论证策略师配置 |
| `agents/tech_writer.jsonc` | 科技写作员配置 |
| `agents/reviewer.jsonc` | 审稿人配置（防幻觉防线二） |
| `tools/validate_report.py` | 数值白名单校验器（防幻觉防线一），可独立运行 |
| `data/qe_results.md` | 仿真数据输入模板（队友填写） |
| `data/derived_formulas.md` | 推导公式声明（数据文件外允许出现的推导值约定） |
| `.env` | 千问 API 密钥（不入库，从 .env.example 复制） |
| `output/` | 成稿输出目录（不入库） |
| `knowledge/` | 可选：放置项目背景资料供 Agent 检索 |

## 数据填写注意事项

1. `data/qe_results.md` 第 3 节对照表是论证核心，尽量填全
2. 单位单独放"单位"列，不要混在数值里；对照表第 1 列固定为物理量名称
   （校验器只解析数据列，名称列中的化学式下标数字不会污染白名单）
3. 第 6 节"队友备注"最关键——把只有仿真人员知道的结论和写作红线写进去
   （备注中的数值+单位也会进入白名单）
4. 留空的项 Agent 会标注"未提供"，不会编造
5. 数据文件只放数据本身，不要写示例值或说明性数字——文件中出现的任何
   数值都会被视为"允许进入报告"的白名单成员

## 环境要求

- Python 3.10–3.13
- crewai==1.15.10、crewai-tools==1.15.10（见 pyproject.toml）
- 通义千问 API Key（DashScope OpenAI 兼容模式）
