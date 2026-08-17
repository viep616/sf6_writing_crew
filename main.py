"""Flow 编排入口（替代原 run.py 的命令式串联）。

结构（固定两轮对抗，无无限循环风险）：

    @start load_data        判道：raw_vasp 有 OUTCAR → 通道 B 自动解析；否则通道 A 手填
    @listen writing_crew    主写作 Crew（4 Agent，qwen-plus）→ 拆分终稿/审稿意见
    @listen adversarial_r1  对抗评审 Crew（3 Agent，qwen3.8-max）→ 裁决书 JSON
    @router route_r1        成立裁决>0 → revise；否则 → finalize
    @listen("revise")       tech_writer 按裁决最小修订（qwen-plus）
    @listen adversarial_r2  修订稿再对抗一轮
    @router route_r2        仍有成立裁决 → 标记「未通过对抗」；→ finalize
    @listen("finalize")     图表生成 → 插图 → 数值校验 → PDF → 时间戳留档

两道防线与对抗的分工：对抗组抓语义/逻辑/物理（LLM），数值白名单校验（代码）
在 finalize 阶段兜底，二者缺一不可。

用法（项目根目录）：
    & "..\.venv\Scripts\python.exe" main.py
或兼容旧入口：
    & "..\.venv\Scripts\python.exe" run.py
"""

import os
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 将 crewai 的运行数据目录重定向到项目内 .appdata/（须在首次 import crewai 前 patch）
import crewai_core.paths as _crewai_paths  # noqa: E402

_RUN_DATA_DIR = str(BASE_DIR / ".appdata")
_crewai_paths.db_storage_path = lambda: _RUN_DATA_DIR
# 保存真实用户目录：MiKTeX（xelatex）子进程需要它们，否则回退写 C:\ProgramData 被沙箱拦截
_REAL_USER_ENV = {k: os.environ[k] for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE") if k in os.environ}
os.environ["APPDATA"] = _RUN_DATA_DIR
os.environ["LOCALAPPDATA"] = _RUN_DATA_DIR
os.environ["USERPROFILE"] = _RUN_DATA_DIR

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE_DIR / ".env", override=True)

from crewai.flow.flow import Flow, listen, router, start  # noqa: E402
from crewai.project.crew_loader import load_crew  # noqa: E402
from pydantic import BaseModel  # noqa: E402

sys.path.insert(0, str(BASE_DIR / "tools"))
import make_charts  # noqa: E402
import md2pdf  # noqa: E402
import parse_vasp  # noqa: E402
import validate_report  # noqa: E402

md2pdf.REAL_USER_ENV.update(_REAL_USER_ENV)

DATA_FILE = BASE_DIR / "data" / "vasp_results.md"
RAW_DIR = BASE_DIR / "data" / "raw_vasp"
OUTPUT_DIR = BASE_DIR / "output"
REVIEW_SPLIT = "=== 审稿意见 ==="
VERDICT_FILE = OUTPUT_DIR / "对抗_裁决书.json"


class PaperState(BaseModel):
    """Flow 共享状态：每个步骤读写，替代散落的模块级变量。"""

    stamp: str = ""
    channel: str = ""            # 输入通道 A（手填）/ B（自动解析）
    round_no: int = 0            # 已执行的对抗轮数
    failed_verdicts: int = 0     # 最后一轮成立（未消解）的裁决数
    adversarial_flag: str = ""   # 留档后缀："" / "_未通过对抗"


def split_review_output() -> None:
    """把主 Crew 评审任务的两段式输出拆为 论文_终稿.md 与 论文_审稿意见.md。"""
    final = OUTPUT_DIR / "论文_终稿.md"
    if not final.is_file():
        return
    text = final.read_text(encoding="utf-8")
    if REVIEW_SPLIT in text:
        paper, notes = text.split(REVIEW_SPLIT, 1)
        for marker in ("=== 论文终稿 ===", "===论文终稿==="):
            paper = paper.replace(marker, "")
        final.write_text(paper.strip() + "\n", encoding="utf-8")
        (OUTPUT_DIR / "论文_审稿意见.md").write_text(notes.strip() + "\n", encoding="utf-8")
        print(f"[拆分] 论文_终稿.md（{len(paper)} 字符）+ 论文_审稿意见.md（{len(notes)} 字符）")
    else:
        print("[警告] 评审输出未包含分隔符，论文_终稿.md 保留原样")


def parse_verdicts() -> int:
    """解析裁决书 JSON，返回成立裁决数。解析失败按 0 处理并留原始文件供人工审。"""
    if not VERDICT_FILE.is_file():
        print("[对抗] 裁决书缺失，按无成立裁决处理")
        return 0
    import json
    raw = VERDICT_FILE.read_text(encoding="utf-8")
    # 鲁棒剥离：markdown 围栏 / 前后杂文
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        print("[对抗] 裁决书不是 JSON，按无成立裁决处理（原文已留档）")
        return 0
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"[对抗] 裁决书 JSON 解析失败（{e}），按无成立裁决处理")
        return 0
    verdicts = data.get("裁决", [])
    upheld = [v for v in verdicts if v.get("结论") == "成立"]
    print(f"[对抗] 裁决 {len(verdicts)} 条，成立 {len(upheld)} 条；{data.get('轮次总结', '')}")
    return len(upheld)


class PaperFlow(Flow[PaperState]):

    # ---------- 输入层 ----------
    @start()
    def load_data(self):
        self.state.stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if parse_vasp.run(RAW_DIR, DATA_FILE):
            self.state.channel = "B"
            print(f"[输入] 通道 B：raw_vasp 自动解析回填（{self.state.stamp}）")
        else:
            self.state.channel = "A"
            print(f"[输入] 通道 A：使用手填模板 data/vasp_results.md（{self.state.stamp}）")

    # ---------- 主写作 ----------
    @listen(load_data)
    def writing_crew(self):
        crew, inputs = load_crew(BASE_DIR / "crew.jsonc")
        crew.kickoff(inputs=inputs)
        split_review_output()

    # ---------- 对抗评审（固定两轮） ----------
    def _run_adversarial(self) -> int:
        crew, inputs = load_crew(BASE_DIR / "review_crew.jsonc")
        crew.kickoff(inputs=inputs)
        self.state.round_no += 1
        self.state.failed_verdicts = parse_verdicts()
        return self.state.failed_verdicts

    @listen(writing_crew)
    def adversarial_r1(self):
        print(f"===== 对抗评审 第 1 轮 =====")
        return self._run_adversarial()

    @router(adversarial_r1)
    def route_r1(self, upheld: int):
        if upheld > 0:
            print(f"[路由] 第 1 轮 {upheld} 条成立 → 进入修订")
            return "do_revise"
        print("[路由] 第 1 轮全部驳回 → 直接定稿")
        return "finalize"

    @listen("do_revise")
    def revise_paper(self):
        crew, inputs = load_crew(BASE_DIR / "revise_crew.jsonc")
        crew.kickoff(inputs=inputs)
        print("[修订] 已按裁决完成最小修订")

    @listen(revise_paper)
    def adversarial_r2(self):
        print("===== 对抗评审 第 2 轮（修订稿复审）=====")
        return self._run_adversarial()

    @router(adversarial_r2)
    def route_r2(self, upheld: int):
        if upheld > 0:
            self.state.adversarial_flag = "_未通过对抗"
            print(f"[路由] 第 2 轮仍有 {upheld} 条成立，轮次用尽 → 带标记定稿转人工")
        else:
            print("[路由] 第 2 轮全部驳回 → 定稿")
        return "finalize"

    # ---------- 定稿 ----------
    @listen("finalize")
    def finalize_paper(self):
        stamp = self.state.stamp

        # 图表生成 + 程序化插图（图与图注由数据文件确定性生成，不经 LLM；
        # 插图放在对抗循环之后，保证对抗组审的是纯文本稿件）
        figures = make_charts.generate(DATA_FILE, OUTPUT_DIR / "figures")
        final_md = OUTPUT_DIR / "论文_终稿.md"
        if figures and final_md.is_file():
            md2pdf.insert_figures(final_md, figures)

        # 防线一：数值白名单校验
        suffix = ""
        if final_md.is_file():
            ok, problems = validate_report.validate(final_md, DATA_FILE)
            suffix = "" if ok else "_未通过校验"
            archive = OUTPUT_DIR / f"论文_终稿_{stamp}{suffix}{self.state.adversarial_flag}.md"
            archive.write_text(final_md.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[留档] {archive.name}")
            draft = OUTPUT_DIR / "论文_初稿.md"
            if draft.is_file():
                (OUTPUT_DIR / f"论文_初稿_{stamp}.md").write_text(
                    draft.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"[留档] 论文_初稿_{stamp}.md")
            if ok:
                print("[校验] 通过：终稿所有数值均命中白名单或可推导。")
            else:
                lines = [
                    f"运行时间戳：{stamp}",
                    f"发现 {len(problems)} 个白名单外数值（数据文件中不存在且无法推导）：",
                    "",
                    *problems,
                    "",
                    "处理建议：核对数值来源；若为合理推导请在数据文件声明；",
                    "若为模型编造，请人工修订终稿后重新校验。",
                ]
                (OUTPUT_DIR / "校验问题.txt").write_text("\n".join(lines), encoding="utf-8")
                print(f"[校验] 未通过！问题清单见 {OUTPUT_DIR / '校验问题.txt'}")

        # 转 PDF：留档版 + 最新版
        if final_md.is_file():
            archived_pdf = OUTPUT_DIR / f"论文_终稿_{stamp}{suffix}{self.state.adversarial_flag}.pdf"
            if md2pdf.md_to_pdf(final_md, archived_pdf):
                import shutil
                shutil.copyfile(archived_pdf, OUTPUT_DIR / "论文_终稿.pdf")
                print(f"[留档] {archived_pdf.name}")

        print("\n========== Flow 运行结束 ==========")
        print(f"输入通道 {self.state.channel}｜对抗 {self.state.round_no} 轮｜"
              f"未消解裁决 {self.state.failed_verdicts} 条｜校验{'通过' if suffix == '' else '未通过'}")
        return stamp


def main() -> None:
    flow = PaperFlow()
    # 流程图（答辩展示用）；依赖缺失时不阻塞主流程
    try:
        flow.plot(str(OUTPUT_DIR / "flow_plot"))
        print("[流程图] output/flow_plot.html")
    except Exception as e:
        print(f"[流程图] 跳过（{type(e).__name__}: {e}）")
    flow.kickoff()


if __name__ == "__main__":
    main()
