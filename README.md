# SF6 写作智能体（Flow 版）

基于 CrewAI 1.15.10 Flow 编排的多智能体写作系统：读取 VASP 仿真结果，自动撰写论证
本项目纳米传感材料优于对比材料的**中文学术论文（Markdown + 含图 PDF）**，
内置「写作 → 多智能体对抗评审 → 修订 → 定稿」闭环与两道防幻觉防线。

## Flow 架构（main.py）

```
@start load_data      判道：data/raw_vasp 有 OUTCAR → 通道 B 自动解析回填；
                      否则通道 A（手填模板 data/vasp_results.md）
        │
        ▼
writing_crew          主写作 Crew（4 Agent，qwen-plus）
                      data_analyst → argument_builder → tech_writer → reviewer
                      产出 论文_初稿.md / 论文_终稿.md / 论文_审稿意见.md
        │
        ▼
adversarial_r1        对抗评审 Crew（3 Agent，qwen3.8-max）
                      attacker（攻击）→ defender（辩护）→ arbiter（裁决）
                      产出 对抗_攻击清单.md / 对抗_裁决书.json
        │
        ▼ route_r1：成立裁决 > 0？
   ┌────┴────┐
   是        否 ──────────────┐
   ▼                         │
revise_paper                 │
   tech_writer 按裁决最小修订 │
        │                    │
        ▼                    │
adversarial_r2（复审）        │
   │ 仍有成立 → 标记          │
   │ 「_未通过对抗」转人工     │
   └────────┬───────────────┘
            ▼
finalize_paper        图表生成 → 程序化插图 → 数值白名单校验 → PDF → 时间戳留档
```

- 固定两轮对抗（`@router` 标签路由），无无限循环风险；
- 对抗组审**纯文本稿件**（插图在校验前才插入），裁决书为程序可解析的严格 JSON；
- 运行后自动生成 `output/flow_plot.html` 流程图（答辩展示用）。

## 模型分工（混合策略）

| 组 | 模型 | 说明 |
|----|------|------|
| 主写作 Crew + 修订 | qwen-plus | 长文写作质量稳定，成本低 |
| 对抗评审 Crew | qwen3.8-max | 推理强，默认关闭思考模式（`extra_body.enable_thinking=false`） |

## VASP 数据输入（双通道）

| 通道 | 条件 | 说明 |
|------|------|------|
| A（手填） | `data/raw_vasp/` 不存在或无 OUTCAR | 队友按 `data/vasp_results.md` 模板手填数值 |
| B（自动解析） | `data/raw_vasp/` 下有 VASP 输出 | `tools/parse_vasp.py` 自动解析回填模板空白格 |

通道 B 目录约定（放对应文件即可，缺的项自动跳过不覆盖手填值）：

```
data/raw_vasp/
├── GAS_MAIN/SOF2/OUTCAR      # SWNT-OH + 气体  → E(total)
├── GAS_MAIN/SO2/OUTCAR ...
├── GAS_REF/SOF2/OUTCAR       # 孤立气体分子    → E(gas)
├── sub_main/OUTCAR           # 孤立 SWNT-OH    → E(衬底)
├── GAS_MAIN/SOF2/ACF.dat     # Bader 电荷（可选）
└── vasprun.xml               # 带隙（可选）
```

吸附能自动按 `E(ads) = E(衬底+气体) − E(衬底) − E(气体)` 计算；
气体目录名用无下标大写（`SOF2`、`SO2F2`、`SO2`、`H2S`、`CO2`、`H2O`、`N2`、`SF6`）。

## 防幻觉体系（三道防线）

| 防线 | 位置 | 抓什么 | 机制 |
|------|------|--------|------|
| 终审（生成时） | reviewer Agent | 语义措辞：矛盾、歧义、无据外推、主题漂移 | 数值溯源以数据文件为最终依据 + 主题锚定 |
| 对抗（生成后） | attacker / defender / arbiter | 物理逻辑：无据外推、机制错误、选择性报道、红线违规 | 攻击九类检查项 → 只引数据文件证据辩护 → 严格 JSON 裁决 |
| 校验（代码级） | `tools/validate_report.py` | 数字事实：白名单外数值 | 数据文件全量数值白名单 + 差值/和/两位有效数字推导校验 |

**校验规则**：终稿中每个「数值+单位」必须满足其一——
1. 命中白名单（出现在 `data/vasp_results.md` 的表格或散文中）；
2. 可由同单位两个白名单数值相减/相加得到（如 −1.85 − (−0.31) = −1.54 eV）；
3. 上述推导值的两位有效数字表述（1.54 写作 1.5，符合数据文件引用约定）；
4. 其余一律报警（默认策略：**留档不阻断**，文件名加 `_未通过校验` 后缀，
   问题清单见 `output/校验问题.txt`）。

对抗裁决同样留档不阻断：两轮后仍有成立裁决 → 文件名加 `_未通过对抗` 后缀转人工。

## 快速开始

```powershell
# 1. 配置密钥（首次）
Copy-Item .env.example .env
# 编辑 .env，填入千问 API Key（DASHSCOPE_API_KEY）

# 2. 准备数据（二选一）
#    通道 A：编辑 data/vasp_results.md 手填（留空的项 Agent 会跳过）
#    通道 B：把 VASP 输出按上文目录约定放进 data/raw_vasp/

# 3. 运行（在本项目目录下，复用上级目录的 .venv 环境）
& "..\.venv\Scripts\python.exe" main.py    # run.py 仍可用（薄入口）
```

运行结束后查看 `output/`：
- `论文_终稿.pdf`（**成品，含四张数据图**）、`论文_终稿.md`
- `对抗_攻击清单.md`、`对抗_裁决书.json`（对抗评审留痕）
- `论文_审稿意见.md`（终审留痕）、`论文_初稿_时间戳.md`（对比用）
- `论文_终稿_时间戳[_未通过校验][_未通过对抗].md/.pdf`（历史留档）
- `校验问题.txt`（校验未过时生成）、`flow_plot.html`（流程图）

## PDF 管线依赖（可选，只出 md 可跳过）

论文 PDF 由 pandoc + xelatex 生成，图表由 matplotlib 绘制。未安装时自动跳过
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
# 工具可独立运行（不跑 LLM，用于调试）
& "..\.venv\Scripts\python.exe" tools\parse_vasp.py                     # 通道 B 解析
& "..\.venv\Scripts\python.exe" tools\make_charts.py                    # 生成图表
& "..\.venv\Scripts\python.exe" tools\md2pdf.py output\论文_终稿.md     # md 转 PDF
& "..\.venv\Scripts\python.exe" tools\validate_report.py                # 数值校验
```

## 文件说明

| 文件/目录 | 用途 |
|-----------|------|
| `main.py` | Flow 编排入口（判道/对抗循环/路由/定稿留档） |
| `run.py` | 兼容旧入口（转调 main.py） |
| `crew.jsonc` | 主写作 Crew：四任务顺序执行 |
| `review_crew.jsonc` | 对抗评审 Crew：攻击 → 辩护 → 裁决 |
| `revise_crew.jsonc` | 修订 Crew：tech_writer 按裁决最小修订 |
| `agents/data_analyst.jsonc` | 仿真数据分析员（qwen-plus） |
| `agents/argument_builder.jsonc` | 论证策略师（qwen-plus） |
| `agents/tech_writer.jsonc` | 科技写作员（qwen-plus，含 FileReadTool） |
| `agents/reviewer.jsonc` | 终审审稿人（qwen-plus，含 FileReadTool） |
| `agents/attacker.jsonc` | 对抗攻击手（qwen3.8-max，九类检查项） |
| `agents/defender.jsonc` | 对抗辩护人（qwen3.8-max，只引数据文件证据） |
| `agents/arbiter.jsonc` | 对抗裁决人（qwen3.8-max，输出严格 JSON 裁决书） |
| `tools/parse_vasp.py` | VASP 输出自动解析器（通道 B），可独立运行 |
| `tools/validate_report.py` | 数值白名单校验器（代码级防线），可独立运行 |
| `tools/make_charts.py` | 数据可视化图表生成器（四张图，不经 LLM），可独立运行 |
| `tools/md2pdf.py` | 程序化插图 + md→PDF 转换器（pandoc + xelatex），可独立运行 |
| `data/vasp_results.md` | VASP 数据手填模板（通道 A） |
| `data/raw_vasp/` | VASP 原始输出目录（通道 B，按上文约定放置） |
| `.env` | 千问 API 密钥（不入库，从 .env.example 复制） |
| `output/` | 成稿输出目录（不入库） |

## 数据填写注意事项

1. `data/vasp_results.md` 第 3 节对照表是论证核心，尽量填全
2. 单位单独放"单位"列，不要混在数值里；对照表第 1 列固定为物理量名称
   （校验器只解析数据列，名称列中的化学式下标数字不会污染白名单）
3. 第 6 节"队友备注"最关键——把只有仿真人员知道的结论和写作红线写进去
   （备注中的数值+单位也会进入白名单）
4. 第 7 节"方法学自查"决定论文的方法适用边界——未计算的项（过渡态/声子/温度等）
   会写入论文并约束 Agent 不基于它们下结论
5. 提供SF₆ 本体吸附能时，选择性论证会自动对照背景 SF₆ 竞争吸附；
   未提供时论文会声明"未评估"
6. 留空的项 Agent 会标注"未提供"，不会编造；通道 B 解析只回填空白格，
   不覆盖手填值
7. 数据文件只放数据本身，不要写示例值或说明性数字——文件中出现的任何
   数值都会被视为"允许进入报告"的白名单成员

## 环境要求

- Windows（图表中文字体依赖系统自带 SimHei）
- Python 3.10–3.13
- crewai==1.15.10、crewai-tools==1.15.10（见 pyproject.toml）
- 通义千问 API Key（DashScope OpenAI 兼容模式，qwen-plus 与 qwen3.8-max）
- PDF 输出（可选）：matplotlib + pandoc + MiKTeX（见上文「PDF 管线依赖」）
