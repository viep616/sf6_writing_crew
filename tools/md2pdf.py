r"""Markdown 论文 → PDF 转换器（pandoc + xelatex 胶水层）。

职责：
1. insert_figures：把 make_charts 生成的插图以固定位置插入论文终稿
   （锚点=「结果与讨论」节标题后的第一个表格之后；无表格则紧跟标题），
   图与图注均由程序确定性生成，不经过 LLM，杜绝幻觉插图；
2. preprocess：Unicode 上下标字符转 LaTeX 数学模式（SOF₂→SOF$_{2}$、
   10⁻³→10$^{-3}$），规避 SimHei/Latin Modern 缺字形问题；
3. md_to_pdf：调用 pandoc + xelatex 编译 PDF（中文 xeCJK + SimHei）。

外部依赖（均装在 D 盘）：
    D:\pandoc\pandoc-3.6.4\pandoc.exe
    D:\MiKTeX\miktex\bin\x64\xelatex.exe

用法（项目根目录）：
    python tools/md2pdf.py 论文.md [输出.pdf]
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PANDOC = Path(r"D:\pandoc\pandoc-3.6.4\pandoc.exe")
XELATEX = Path(r"D:\MiKTeX\miktex\bin\x64\xelatex.exe")

# 由 run.py 注入的真实用户目录（APPDATA/LOCALAPPDATA/USERPROFILE）。
# run.py 为 crewai 把这些变量重定向到项目 .appdata/ 后，MiKTeX 会找不到
# 用户级配置/格式缓存而回退写 C:\ProgramData（沙箱禁止）；调 pandoc 的
# 子进程须恢复真实值。独立运行本脚本时为空 dict，行为不变。
REAL_USER_ENV: dict[str, str] = {}

SUB_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
SUP_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
ANCHOR_RE = re.compile(r"^#{1,6}\s*\d*[.、]?\s*结果与讨论.*$", re.M)


def preprocess(text: str) -> str:
    """Unicode 上下标序列 → LaTeX 数学模式（连续序列合并成一个组）。"""
    text = re.sub(r"[₀₁₂₃₄₅₆₇₈₉]+",
                  lambda m: "$_{" + m.group().translate(SUB_MAP) + "}$", text)
    text = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+",
                  lambda m: "$^{" + m.group().translate(SUP_MAP) + "}$", text)
    return text


def insert_figures(md_path: Path, figures: list[tuple[str, str]]) -> bool:
    """把图表插入论文 md。幂等：文中已含 figures/ 引用则跳过。

    图片用空 alt（![](path)）避免 pandoc 生成浮动 figure 环境，
    图注以下一段斜体文字呈现，位置完全固定、紧跟表格。
    """
    if not figures or not md_path.is_file():
        return False
    text = md_path.read_text(encoding="utf-8")
    if "figures/" in text or "figures\\" in text:
        print("[插图] 论文已包含图表引用，跳过")
        return False
    block = "\n\n" + "\n\n".join(
        f"![](figures/{fn})\n\n*{cap}*" for fn, cap in figures
    ) + "\n"
    m = ANCHOR_RE.search(text)
    if not m:
        text = text.rstrip("\n") + "\n" + block
        print("[插图] 未找到「结果与讨论」标题，图表已追加至文末")
    else:
        rest = text[m.end():]
        lines = rest.splitlines(keepends=True)
        # 扫描范围：本节内（到下一个标题行为止）
        section_end = next((k for k, l in enumerate(lines) if l.lstrip().startswith("#")), len(lines))
        # 锚点：节内第一个表格块之后；无表格则紧跟标题
        insert_at = 0
        k = 0
        while k < section_end:
            if lines[k].lstrip().startswith("|"):
                while k < section_end and lines[k].lstrip().startswith("|"):
                    k += 1
                insert_at = k
                break
            k += 1
        pos = m.end() + sum(len(l) for l in lines[:insert_at])
        text = text[:pos] + block + text[pos:]
        print(f"[插图] 已插入 {len(figures)} 张图至「结果与讨论」节")
    md_path.write_text(text, encoding="utf-8")
    return True


def md_to_pdf(md_path: Path, pdf_path: Path | None = None) -> bool:
    """论文 md → PDF。返回是否成功（失败不抛异常，由调用方决定留档策略）。"""
    md_path = Path(md_path)
    if not md_path.is_file():
        print(f"[PDF] 输入不存在：{md_path}")
        return False
    if not PANDOC.is_file() or not XELATEX.is_file():
        print(f"[PDF] 缺少 pandoc 或 xelatex，跳过 PDF 转换\n      {PANDOC}\n      {XELATEX}")
        return False
    pdf_path = Path(pdf_path) if pdf_path else md_path.with_suffix(".pdf")

    tmp_md = md_path.with_name(md_path.stem + ".tmp_pdf.md")
    tmp_md.write_text(preprocess(md_path.read_text(encoding="utf-8")), encoding="utf-8")

    cmd = [
        str(PANDOC), tmp_md.name, "-o", pdf_path.name,
        f"--pdf-engine={XELATEX}",
        "-V", "CJKmainfont=SimHei",
        "-V", "geometry:margin=2.5cm",
        "-V", "fontsize=11pt",
        "--resource-path", ".",
    ]
    try:
        env = dict(os.environ)
        env.update(REAL_USER_ENV)  # 恢复真实用户目录，MiKTeX 依赖其定位配置
        r = subprocess.run(cmd, cwd=md_path.parent, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        print("[PDF] xelatex 编译超时（>600s）")
        tmp_md.unlink(missing_ok=True)
        return False
    finally:
        pass
    tmp_md.unlink(missing_ok=True)
    if r.returncode != 0 or not pdf_path.is_file():
        print(f"[PDF] 转换失败（exit {r.returncode}），stderr 摘要：")
        print("\n".join(r.stderr.splitlines()[:15]))
        return False
    size_kb = pdf_path.stat().st_size // 1024
    print(f"[PDF] 已生成 {pdf_path.name}（{size_kb} KB）")
    return True


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    md = Path(sys.argv[1])
    pdf = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    return 0 if md_to_pdf(md, pdf) else 1


if __name__ == "__main__":
    sys.exit(main())
