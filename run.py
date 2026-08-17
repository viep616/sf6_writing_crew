"""直接运行入口。

绕过 crewai CLI 的 uv 环境管理与交互式 TUI，
复用项目根目录（挑战杯/.venv）已装好的 crewai 环境直接运行 crew.jsonc。

流程：crew 运行 → 拆分评审 Agent 的两段式输出（论文终稿 + 审稿意见）
     → 图表生成（tools/make_charts.py，数据驱动不经 LLM）
     → 程序化插图（tools/md2pdf.py，图与图注均来自数据文件）
     → 数值防幻觉校验（tools/validate_report.py，覆盖含图注的终稿全文）
     → 转 PDF（pandoc + xelatex）
     → 时间戳留档（未通过校验的文件名加「_未通过校验」后缀，md 与 PDF 同名成对）

用法（在本项目目录下）：
    & "..\\.venv\\Scripts\\python.exe" run.py
"""

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 将 crewai 的运行数据目录（任务输出数据库等）重定向到项目内 .appdata/，
# 避免写入系统 AppData\Local\CrewAI 与 ~/.config/crewai（沙箱/权限受限环境下无法写入）。
# 注意：必须在首次 import crewai 之前完成 patch（下游模块会 from ... import 该函数）。
import crewai_core.paths as _crewai_paths

_RUN_DATA_DIR = str(BASE_DIR / ".appdata")
_crewai_paths.db_storage_path = lambda: _RUN_DATA_DIR
# 保存真实用户目录：MiKTeX（xelatex）依赖它们定位用户级配置/格式缓存，
# 子进程调 pandoc 时需恢复，否则 MiKTeX 回退写 C:\ProgramData（沙箱禁止）
_REAL_USER_ENV = {k: os.environ[k] for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE") if k in os.environ}
os.environ["APPDATA"] = _RUN_DATA_DIR
os.environ["LOCALAPPDATA"] = _RUN_DATA_DIR
os.environ["USERPROFILE"] = _RUN_DATA_DIR

from dotenv import load_dotenv

load_dotenv(BASE_DIR / ".env", override=True)

from crewai.project.crew_loader import load_crew

sys.path.insert(0, str(BASE_DIR / "tools"))
import make_charts  # noqa: E402
import md2pdf  # noqa: E402
import validate_report  # noqa: E402

md2pdf.REAL_USER_ENV.update(_REAL_USER_ENV)

REVIEW_SPLIT = "=== 审稿意见 ==="


def split_review_output() -> bool:
    """把评审任务输出（两段式）拆分为 论文_终稿.md 与 论文_审稿意见.md。

    返回终稿文件是否存在。
    """
    final = BASE_DIR / "output" / "论文_终稿.md"
    if not final.is_file():
        return False
    text = final.read_text(encoding="utf-8")
    if REVIEW_SPLIT in text:
        paper, notes = text.split(REVIEW_SPLIT, 1)
        # 去掉终稿段开头可能残留的「=== 论文终稿 ===」标记与首尾空白
        for marker in ("=== 论文终稿 ===", "===论文终稿==="):
            paper = paper.replace(marker, "")
        paper = paper.strip() + "\n"
        notes = notes.strip() + "\n"
        final.write_text(paper, encoding="utf-8")
        (BASE_DIR / "output" / "论文_审稿意见.md").write_text(notes, encoding="utf-8")
        print(f"[拆分] 论文_终稿.md（{len(paper)} 字符）+ 论文_审稿意见.md（{len(notes)} 字符）")
    else:
        print("[警告] 评审输出未包含分隔符，论文_终稿.md 保留原样（无审稿意见文件）")
    return True


def run_validation_gate(stamp: str) -> str:
    """对论文终稿执行数值校验，并按结果决定留档文件名后缀。返回后缀字符串。"""
    report = BASE_DIR / "output" / "论文_终稿.md"
    data = BASE_DIR / "data" / "qe_results.md"
    if not report.is_file():
        return ""
    ok, problems = validate_report.validate(report, data)

    suffix = "" if ok else "_未通过校验"
    archive = BASE_DIR / "output" / f"论文_终稿_{stamp}{suffix}.md"
    archive.write_text(report.read_text(encoding="utf-8"), encoding="utf-8")

    # 初稿留档（无需校验，初稿仅作对比用）
    draft = BASE_DIR / "output" / "论文_初稿.md"
    if draft.is_file():
        draft_archive = BASE_DIR / "output" / f"论文_初稿_{stamp}.md"
        draft_archive.write_text(draft.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[留档] {draft_archive.name}")

    if ok:
        print(f"[留档] {archive.name}")
        print("[校验] 通过：终稿所有数值均命中白名单或可推导。")
    else:
        problem_file = BASE_DIR / "output" / "校验问题.txt"
        lines = [
            f"运行时间戳：{stamp}",
            f"发现 {len(problems)} 个白名单外数值（数据文件中不存在且无法推导）：",
            "",
            *problems,
            "",
            "处理建议：核对上述数值来源；若确为合理推导，请在 data/derived_formulas.md 声明公式；",
            "若为模型编造，请人工修订终稿后重新校验。",
        ]
        problem_file.write_text("\n".join(lines), encoding="utf-8")
        print(f"[留档] {archive.name}（带「未通过校验」标记）")
        print(f"[校验] 未通过！问题清单见 {problem_file}")
    return suffix


def main() -> None:
    crew, default_inputs = load_crew(BASE_DIR / "crew.jsonc")
    result = crew.kickoff(inputs=default_inputs)

    split_review_output()

    # 图表生成 + 程序化插图（图与图注均由数据文件确定性生成，不经 LLM）
    figures = make_charts.generate(BASE_DIR / "data" / "vasp_results.md",
                                   BASE_DIR / "output" / "figures")
    final_md = BASE_DIR / "output" / "论文_终稿.md"
    if figures and final_md.is_file():
        md2pdf.insert_figures(final_md, figures)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = run_validation_gate(stamp)

    # 转 PDF：留档版（与 md 留档同名成对）+ 最新版（论文_终稿.pdf）
    if final_md.is_file():
        archived_pdf = BASE_DIR / "output" / f"论文_终稿_{stamp}{suffix}.pdf"
        if md2pdf.md_to_pdf(final_md, archived_pdf):
            shutil.copyfile(archived_pdf, BASE_DIR / "output" / "论文_终稿.pdf")
            print(f"[留档] {archived_pdf.name}")

    print("\n========== Crew 运行结束 ==========")
    print(result)


if __name__ == "__main__":
    main()
