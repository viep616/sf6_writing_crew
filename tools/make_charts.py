"""数据可视化图表生成器（PDF 管线配套）。

从 data/qe_results.md 的「关键物理量对照表」解析数值，生成四张论文插图：
  图1 目标/干扰气体吸附能分组柱状图（fig1_adsorption_energy.png）
  图2 吸附前后带隙对比（fig2_bandgap.png）
  图3 电荷转移量 + 功函数变化双面板（fig3_charge_workfunc.png）
  图4 吸附能热力图（fig4_adsorption_heatmap.png）

设计原则（防幻觉一致）：图表只画数据文件中存在的数值，缺项留空不补；
任何一张图缺少有效数据时自动跳过并打印提示，不阻塞主流程。

用法（项目根目录）：
    python tools/make_charts.py [数据文件] [输出目录]
不带参数时用默认路径 data/qe_results.md → output/figures/。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA = BASE_DIR / "data" / "vasp_results.md"
DEFAULT_OUT = BASE_DIR / "output" / "figures"

# 中文字体（SimHei 无 Unicode 下标字形，气体式用 mathtext 渲染下标）
font_manager.fontManager.addfont(r"C:\Windows\Fonts\simhei.ttf")
plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False

SUB_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
SUB_SEQ = re.compile(r"[₀₁₂₃₄₅₆₇₈₉]+")

COLOR_MAIN = "#2b6cb0"   # 本项目材料
COLOR_REF = "#a0aec0"    # 对比材料
DPI = 200

# 气体名行首模式：如 "SOF₂ 吸附能" / "SF₆ 本体吸附能"
GAS_PREFIX_RE = re.compile(r"^\s*([A-Za-z]{1,3}(?:[₀-₉][A-Za-z]{0,2})*)\s+")
CELL_NUM_RE = re.compile(r"[−\-–]?\d+(?:\.\d+)?")


def tex(name: str) -> str:
    """把 Unicode 下标字符序列转成 mathtext（SOF₂ → SOF$_{2}$），SimHei 缺下标字形。"""
    return SUB_SEQ.sub(lambda m: "$_{" + m.group().translate(SUB_MAP) + "}$", name)


def cell_values(cell: str) -> list[float]:
    """提取单元格内全部数值；'未测'/'—'/空 → []。"""
    if not cell or re.search(r"未|—|^-+$", cell):
        return []
    vals = []
    for m in CELL_NUM_RE.finditer(cell):
        vals.append(float(m.group().replace("−", "-").replace("–", "-")))
    return vals


def cell_first(cell: str) -> float | None:
    """单元格首个数值（主值），如 '0.38（收窄 0.13）' → 0.38。"""
    vals = cell_values(cell)
    return vals[0] if vals else None


def parse_table(text: str):
    """解析「关键物理量对照表」。

    返回 (main_name, ref_name, rows)，rows = [(物理量名, 本项目单元格, 对比单元格, 单位)]。
    仅收 4 列且首列为'物理量'表头的表格；表头括号内提取材料简称。
    """
    lines = text.splitlines()
    main_name, ref_name, rows = "本项目材料", "对比材料", []
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0] != "物理量":
            continue
        for label, idx in ((cells[1], "main"), (cells[2], "ref")):
            m = re.search(r"[（(]([^（()）]+)[)）]", label)
            short = m.group(1).strip() if m else label
            if idx == "main":
                main_name = short or main_name
            else:
                ref_name = short or ref_name
        for row in lines[i + 2:]:
            if not row.strip().startswith("|"):
                break
            rc = [c.strip() for c in row.strip().strip("|").split("|")]
            if len(rc) >= 4:
                rows.append((rc[0], rc[1], rc[2], rc[3]))
        break
    return main_name, ref_name, rows


def gas_of(row_name: str) -> str | None:
    """从吸附能行名提取气体：优先括号（干扰气体1吸附能（CO₂）），否则行首 token（SOF₂ 吸附能）。"""
    m = re.search(r"[（(]([^（()）]+)[)）]", row_name)
    if m:
        inner = m.group(1).split("，")[0].split(",")[0].strip()
        if re.match(r"^[A-Za-z]", inner):  # '吸附 SOF₂ 后' 这类中文短语排除
            return inner
    m2 = GAS_PREFIX_RE.match(row_name)
    return m2.group(1) if m2 else None


def collect_adsorption(rows):
    """收集吸附能行 → [(气体名, 是否背景/干扰, main值, ref值)]。"""
    out = []
    for name, main, ref, unit in rows:
        if "吸附能" not in name or unit not in ("eV", "ev"):
            continue
        gas = gas_of(name)
        if gas is None:
            print(f"[图表] 跳过无法识别气体名的行：{name}")
            continue
        if "本体" in name:
            gas += "本体"
        is_bg = ("干扰" in name) or ("本体" in name)
        out.append((gas, is_bg, cell_first(main), cell_first(ref)))
    return out


def collect_charge(rows):
    """收集电荷转移量行 → [(气体名, main值, ref值)]，多值行（SO₂F₂ / SO₂ / H₂S）按序展开。"""
    out = []
    for name, main, ref, unit in rows:
        if "电荷转移量" not in name or unit != "e":
            continue
        m = re.search(r"[（(]([^（()）]+)[)）]", name)
        if not m:
            print(f"[图表] 跳过无法识别气体的电荷转移行：{name}")
            continue
        # 先 strip 再过滤（否则前导空格导致 isalpha 失败）；每段去掉'，Bader'类后缀
        gases = []
        for seg in re.split(r"[/／、]", m.group(1)):
            seg = seg.strip().split("，")[0].split(",")[0].strip()
            if seg and seg[0].isascii() and seg[0].isalpha():
                gases.append(seg)
        mvals, rvals = cell_values(main), cell_values(ref)
        if len(gases) != len(mvals):
            print(f"[图表] 跳过气体数与数值数不匹配的行：{name}")
            continue
        for i, (gas, mv) in enumerate(zip(gases, mvals)):
            rv = rvals[i] if len(rvals) == len(gases) else None
            out.append((gas, mv, rv))
    return out


def _save(fig, out_dir: Path, fname: str) -> str:
    fig.tight_layout()
    fig.savefig(out_dir / fname, dpi=DPI)
    plt.close(fig)
    return fname


def generate(data_path: Path = DEFAULT_DATA, out_dir: Path = DEFAULT_OUT) -> list[tuple[str, str]]:
    """生成全部图表。返回 [(文件名, 图注), ...]，仅供实际生成的图。"""
    if not data_path.is_file():
        print(f"[图表] 数据文件不存在，跳过图表生成：{data_path}")
        return []
    out_dir.mkdir(parents=True, exist_ok=True)

    main_name, ref_name, rows = parse_table(data_path.read_text(encoding="utf-8"))
    figures: list[tuple[str, str]] = []

    # ---------- 图1：吸附能分组柱状图 ----------
    ads = collect_adsorption(rows)
    if ads:
        labels = [tex(a[0]) for a in ads]
        x = range(len(ads))
        w = 0.38
        fig, ax = plt.subplots(figsize=(6.6, 3.6))
        b1 = ax.bar([i - w / 2 for i in x], [v[2] if v[2] is not None else float("nan") for v in ads], w,
                    label=tex(main_name), color=COLOR_MAIN)
        b2 = ax.bar([i + w / 2 for i in x], [v[3] if v[3] is not None else float("nan") for v in ads], w,
                    label=tex(ref_name), color=COLOR_REF)
        for bars in (b1, b2):
            ax.bar_label(bars, fmt="%.2f", fontsize=7.5, padding=2)
        # 目标气体区与背景/干扰气体区分隔线
        bg_idx = [i for i, a in enumerate(ads) if a[1]]
        if bg_idx and bg_idx[0] > 0:
            ax.axvline(bg_idx[0] - 0.5, color="#718096", ls="--", lw=0.8)
            ax.text(bg_idx[0] - 0.5, ax.get_ylim()[1], " 背景气体", fontsize=8, color="#718096", va="top")
        ax.axhline(0, color="black", lw=0.8)
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylabel("吸附能 $E_{ads}$ (eV)")
        ax.legend(fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
        fname = _save(fig, out_dir, "fig1_adsorption_energy.png")
        figures.append((fname, f"图 1　{main_name} 与 {ref_name} 对目标气体及背景气体的吸附能对比（虚线右侧为背景/干扰气体）"))

    # ---------- 图2：带隙对比 ----------
    gap_rows = [(n, cell_first(m), cell_first(r)) for n, m, r, u in rows if "带隙" in n and u == "eV"]
    gap_rows = [g for g in gap_rows if g[1] is not None or g[2] is not None]
    if gap_rows:
        labels = ["本征带隙" if "本征" in n else tex(n.replace("带隙", "").strip()) for n, _, _ in gap_rows]
        x = range(len(gap_rows))
        w = 0.38
        fig, ax = plt.subplots(figsize=(4.4, 3.2))
        b1 = ax.bar([i - w / 2 for i in x], [g[1] if g[1] is not None else float("nan") for g in gap_rows], w,
                    label=tex(main_name), color=COLOR_MAIN)
        b2 = ax.bar([i + w / 2 for i in x], [g[2] if g[2] is not None else float("nan") for g in gap_rows], w,
                    label=tex(ref_name), color=COLOR_REF)
        for bars in (b1, b2):
            ax.bar_label(bars, fmt="%.2f", fontsize=8, padding=2)
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylabel("带隙 (eV)")
        ax.legend(fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
        fname = _save(fig, out_dir, "fig2_bandgap.png")
        figures.append((fname, f"图 2　{main_name} 与 {ref_name} 吸附前后的带隙对比"))

    # ---------- 图3：电荷转移 + 功函数双面板 ----------
    charges = collect_charge(rows)
    wf = next(((cell_first(m), cell_first(r)) for n, m, r, u in rows if "功函数" in n and u == "eV"), None)
    if charges or wf:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.6, 3.4), width_ratios=[2, 1])
        if charges:
            labels = [tex(c[0]) for c in charges]
            x = range(len(charges))
            w = 0.38
            b1 = ax1.bar([i - w / 2 for i in x], [c[1] if c[1] is not None else float("nan") for c in charges], w,
                         label=tex(main_name), color=COLOR_MAIN)
            b2 = ax1.bar([i + w / 2 for i in x], [c[2] if c[2] is not None else float("nan") for c in charges], w,
                         label=tex(ref_name), color=COLOR_REF)
            for bars in (b1, b2):
                ax1.bar_label(bars, fmt="%.3f", fontsize=7.5, padding=2)
            ax1.axhline(0, color="black", lw=0.8)
            ax1.set_xticks(list(x))
            ax1.set_xticklabels(labels, fontsize=9)
            ax1.set_ylabel("电荷转移量 (e)")
            ax1.set_title("电荷转移量（正值：材料→气体）", fontsize=9)
            ax1.legend(fontsize=8)
            ax1.spines[["top", "right"]].set_visible(False)
        else:
            ax1.axis("off")
        if wf and (wf[0] is not None or wf[1] is not None):
            names, vals = [main_name, ref_name], [wf[0], wf[1]]
            colors = [COLOR_MAIN, COLOR_REF]
            keep = [i for i, v in enumerate(vals) if v is not None]
            bars = ax2.bar([tex(names[i]) for i in keep], [vals[i] for i in keep],
                           color=[colors[i] for i in keep], width=0.5)
            ax2.bar_label(bars, fmt="%.2f", fontsize=8, padding=2)
            ax2.axhline(0, color="black", lw=0.8)
            ax2.set_ylabel("$\\Delta\\Phi$ (eV)")
            ax2.set_title("功函数变化", fontsize=9)
            ax2.tick_params(axis="x", labelsize=8)
            ax2.spines[["top", "right"]].set_visible(False)
        else:
            ax2.axis("off")
        fname = _save(fig, out_dir, "fig3_charge_workfunc.png")
        figures.append((fname, f"图 3　{main_name} 与 {ref_name} 吸附目标气体后的电荷转移量与功函数变化"))

    # ---------- 图4：吸附能热力图 ----------
    if ads:
        gases = [a[0] for a in ads]
        mat = [[abs(a[2]) if a[2] is not None else float("nan") for a in ads],
               [abs(a[3]) if a[3] is not None else float("nan") for a in ads]]
        fig, ax = plt.subplots(figsize=(6.6, 2.2))
        im = ax.imshow(mat, cmap="Reds", aspect="auto", vmin=0)
        ax.set_xticks(range(len(gases)))
        ax.set_xticklabels([tex(g) for g in gases], fontsize=9)
        ax.set_yticks([0, 1])
        ax.set_yticklabels([tex(main_name), tex(ref_name)], fontsize=9)
        for i in range(2):
            for j in range(len(gases)):
                v = ads[j][2] if i == 0 else ads[j][3]
                if v is None:
                    ax.text(j, i, "—", ha="center", va="center", fontsize=8, color="#718096")
                else:
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=8,
                            color="white" if abs(v) > 1.0 else "black")
        cbar = fig.colorbar(im, ax=ax, shrink=0.85)
        cbar.set_label("|$E_{ads}$| (eV)", fontsize=9)
        fname = _save(fig, out_dir, "fig4_adsorption_heatmap.png")
        figures.append((fname, f"图 4　吸附能热力图（颜色越深吸附越强，— 表示未提供）"))

    for fn, _ in figures:
        print(f"[图表] 生成 {out_dir / fn}")
    if not figures:
        print("[图表] 数据中无可绘制的物理量，未生成任何图")
    return figures


def main() -> int:
    data = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATA
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    figures = generate(data, out)
    return 0 if figures else 1


if __name__ == "__main__":
    sys.exit(main())
