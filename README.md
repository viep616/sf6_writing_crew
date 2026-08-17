# SF6 写作智能体 Crew

基于 CrewAI 1.15.10 的四智能体协作写作系统：读取 QE（Quantum ESPRESSO）仿真结果，
自动撰写论证本项目纳米传感材料优于对比材料的**中文学术论文（Markdown + 含图 PDF）**，
并内置两道防幻觉防线与物理严谨性约束。

## 智能体架构

```
data/qe_results.md（队友填写的仿真摘要）
        │
        ▼
 [data_analyst]      仿真数据分析员 → 物理量对照分析 + 方法学边界
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
 图表生成             tools/make_charts.py 数据驱动生成四张图（不经 LLM，缺项留空不编造）
        │
        ▼
 程序化插图           tools/md2pdf.py 固定插入「结果与讨论」对比表之后
        │
        ▼
 防线一（代码级）    tools/validate_report.py 数值白名单校验
                     全过   → 正常时间戳留档
                     未过   → 留档文件名加「_未通过校验」+ output/校验问题.txt
        │
        ▼
 PDF 输出            pandoc + xelatex → 论文_终稿.pdf（md 与 PDF 同名成对留档）
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

运行结束后查看 `output/`：`论文_终稿.pdf`（**成品，含四张数据图**）、`论文_终稿.md`、
`论文_审稿意见.md`（评审留痕）、`论文_初稿_时间戳.md`（对比用）、
`论文_终稿_时间戳[.md|.pdf|_未通过校验.md]`（历史留档，md 与 PDF 同名成对）、
`figures/`（图表 PNG 源文件）。

## PDF 管线依赖（可选，只出 md 可跳过）

论文 PDF 由 pandoc + xelatex 生成，图表由 matplotlib 绘制。未安装时 run.py 自动跳过
PDF 转换（打印提示），md 全流程不受影响。

| 依赖 | 安装方式 | 用途 |
|------|---------|------|
| matplotlib | `pip install matplotlib` | 数据可视化图表（SimHei 中文字体，Windows 自带） |
| pandoc ≥ 3.0 | [GitHub Releases](https://github.com/jgm/pandoc/releases) 下载 zip 便携版解压 | md → LaTeX 中间层 |
| MiKTeX（Basic 即可） | [官网](https://miktex.org/download) 安装，装完运行 `mpm --install=xecjk` 补中文宏包 | xelatex 编译 PDF |

**路径约定**：`tools/md2pdf.py` 顶部两个常量指定了 pandoc 与 xelatex 的位置（默认
`D:\pandoc\pandoc-3.6.4\pandoc.exe`、`D:\MiKTeX\miktex\bin\x64\xelatex.exe`）。
装到其他路径的，改这两个常量即可。

```powershell
# 图表生成与 PDF 转换可独立运行（不跑 LLM，用于调试）
& "..\.venv\Scripts\python.exe" tools\make_charts.py                 # 生成图表
& "..\.venv\Scripts\python.exe" tools\md2pdf.py output\论文_终稿.md  # md 转 PDF
```

## 文件说明

| 文件/目录 | 用途 |
|-----------|------|
| `crew.jsonc` | 任务链与 Crew 设置（四任务顺序执行） |
| `agents/data_analyst.jsonc` | 仿真数据分析员配置 |
| `agents/argument_builder.jsonc` | 论证策略师配置 |
| `agents/tech_writer.jsonc` | 科技写作员配置 |
| `agents/reviewer.jsonc` | 审稿人配置（防幻觉防线二） |
| `tools/validate_report.py` | 数值白名单校验器（防幻觉防线一），可独立运行 |
| `tools/make_charts.py` | 数据可视化图表生成器（四张图，数据驱动不经 LLM），可独立运行 |
| `tools/md2pdf.py` | 程序化插图 + md→PDF 转换器（pandoc + xelatex），可独立运行 |
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
4. 第 7 节"方法学自查"决定论文的方法适用边界——未计算的项（过渡态/声子/温度等）
   会写入论文并约束 Agent 不基于它们下结论
5. 第 8 节"完成度自查"声明数据状态，"待补数据清单"会体现在论文局限性中
6. 提供SF₆ 本体吸附能时，选择性论证会自动对照背景 SF₆ 竞争吸附；
   未提供时论文会声明"未评估"
7. 留空的项 Agent 会标注"未提供"，不会编造
8. 数据文件只放数据本身，不要写示例值或说明性数字——文件中出现的任何
   数值都会被视为"允许进入报告"的白名单成员

## 环境要求

- Windows（图表中文字体依赖系统自带 SimHei）
- Python 3.10–3.13
- crewai==1.15.10、crewai-tools==1.15.10（见 pyproject.toml）
- 通义千问 API Key（DashScope OpenAI 兼容模式）
- PDF 输出（可选）：matplotlib + pandoc + MiKTeX（见上文「PDF 管线依赖」）
