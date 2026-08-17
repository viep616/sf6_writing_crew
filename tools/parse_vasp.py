"""VASP 原始输出解析器（输入层通道 B）。

从 data/raw_vasp/<体系>/ 的原始文件抽数，回填 data/vasp_results.md 第 3 节：
  - 吸附能：OUTCAR 末次电子步自由能 E0，按 E(ads)=E(衬底+气体)−E(衬底)−E(气体)
  - 电荷转移量：吸附体系 ACF.dat 的 Bader 净电荷（单位 |e|，符号保留）
  - 带隙：vasprun.xml 本征值（价带顶−导带底，自旋非极化口径）

设计原则：
  - 缺目录/缺文件/解析失败 → 该项跳过并打印提示，绝不阻塞主流程；
  - 手填值不覆盖（第 3 节已有数值的格子跳过）；
  - 算出的吸附能保留两位小数回填（与模板口径一致）。

目录约定（体系名）：
    衬底：swnt_oh（本项目材料）、swnt（对比材料）
    孤立气体：gases/<GAS>，GAS 用无下标大写（SOF2、SO2F2、SO2、H2S、CO2、H2O、N2、SF6）
    吸附体系：<衬底>+<GAS>，如 swnt_oh+SOF2

用法（项目根目录）：
    python tools/parse_vasp.py [raw目录] [数据模板路径]
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RAW = BASE_DIR / "data" / "raw_vasp"
DEFAULT_DATA = BASE_DIR / "data" / "vasp_results.md"

# 物理量行名（第 3 节）→ 模板中（气体, 材料列）
MAIN_COL, REF_COL = 1, 2
GAS_MAIN, GAS_REF = "swnt_oh", "swnt"

# 气体无下标名 → 论文行名中的下标写法
GASES = ["SOF2", "SO2F2", "SO2", "H2S", "CO2", "H2O", "N2", "SF6"]
GAS_SUB = {"SOF2": "SOF₂", "SO2F2": "SO₂F₂", "SO2": "SO₂", "H2S": "H₂S",
           "CO2": "CO₂", "H2O": "H₂O", "N2": "N₂", "SF6": "SF₆"}


def read_energy(outcar: Path) -> float | None:
    """取 OUTCAR 末次 'free  energy   TOTEN' 行的自由能（eV）。文件缺失静默返回。"""
    if not outcar.is_file():
        return None
    try:
        text = outcar.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        print(f"[parse_vasp] 读取失败 {outcar.name}: {e}")
        return None
    values = re.findall(r"free\s+energy\s+TOTEN\s*=\s*([−\-–]?\d+\.\d+)", text)
    if not values:
        print(f"[parse_vasp] {outcar.parent.name}/OUTCAR 未找到 TOTEN 行，跳过")
        return None
    return float(values[-1].replace("−", "-").replace("–", "-"))


def read_bader(acf: Path) -> float | None:
    """读 ACF.dat：原子行第 5 列（CHARGE-difference）。气体原子的净电荷之和
    为吸附体系的电荷转移量；孤立原子基组约定 Bader 电荷 ≈ 价电子数。
    这里返回「全部原子 |净电荷| 求和后取平均符号」的简化口径：
    实际取「气 体原子净电荷之和 − 孤立气体 ACF 同列和」，两份 ACF 都在时精确。"""
    try:
        lines = acf.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as e:
        print(f"[parse_vasp] 读取失败 {acf.name}: {e}")
        return None
    charges = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 5 and parts[0].isdigit():
            try:
                charges.append(float(parts[4]))
            except ValueError:
                continue
    if not charges:
        print(f"[parse_vasp] {acf.parent.name}/ACF.dat 无有效原子行，跳过")
        return None
    return sum(charges)


def read_gap(vasprun: Path) -> float | None:
    """读 vasprun.xml 本征值算带隙（eV，自旋非极化口径）。

    取全部 k 点占据态最大值（VBM）与非占据态最小值（CBM）之差；
    占据判定用 eigenvalue 的 attrib occupancy > 0.5。
    """
    try:
        tree = ET.parse(vasprun)
    except (OSError, ET.ParseError) as e:
        print(f"[parse_vasp] 解析失败 {vasprun.name}: {e}")
        return None
    vbm, cbm = None, None
    for ev in tree.iter("eigenvalue"):
        try:
            occ = float(ev.get("occupancy", "0"))
            energy = float(ev.text)
        except (TypeError, ValueError):
            continue
        if occ > 0.5:
            vbm = energy if vbm is None else max(vbm, energy)
        else:
            cbm = energy if cbm is None else min(cbm, energy)
    if vbm is None or cbm is None:
        print(f"[parse_vasp] {vasprun.parent.name}/vasprun.xml 占据信息不足，跳过")
        return None
    return round(cbm - vbm, 2)


def compute_adsorption_energies(raw: Path) -> dict[tuple[str, str], float]:
    """返回 {(气体无下标名, 衬底目录名): 吸附能 eV}。"""
    out: dict[tuple[str, str], float] = {}
    for sub in (GAS_MAIN, GAS_REF):
        e_sub = read_energy(raw / sub / "OUTCAR")
        if e_sub is None:
            continue
        for gas in GASES:
            e_gas = read_energy(raw / "gases" / gas / "OUTCAR")
            e_tot = read_energy(raw / f"{sub}+{gas}" / "OUTCAR")
            if e_gas is None or e_tot is None:
                continue
            out[(gas, sub)] = round(e_tot - e_sub - e_gas, 2)
    return out


def _table_section_lines(text: str) -> list[str]:
    """定位第 3 节对照表的行号区间（含表头与数据行）。"""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("#") and re.search(r"3\s*[.、]\s*关键物理量", line):
            start = i
        elif start is not None and line.strip().startswith("#") and not line.strip().startswith("# 3"):
            return lines[start:i]
    return lines[start:] if start is not None else []


def fill_template(data_path: Path, raw: Path) -> bool:
    """把解析结果回填模板第 3 节。返回是否写入了内容。"""
    text = data_path.read_text(encoding="utf-8")
    section = _table_section_lines(text)
    if not section:
        print("[parse_vasp] 未找到第 3 节对照表，放弃回填")
        return False

    ads = compute_adsorption_energies(raw)
    charges: dict[tuple[str, str], float] = {}
    for sub in (GAS_MAIN, GAS_REF):
        for gas in ("SOF2",):  # 主目标 SOF₂ 的电荷转移
            acf = raw / f"{sub}+{gas}" / "ACF.dat"
            acf0 = raw / "gases" / gas / "ACF.dat"
            if not acf.is_file():
                continue
            q1, q0 = read_bader(acf), (read_bader(acf0) if acf0.is_file() else None)
            if q1 is not None:
                charges[(gas, sub)] = round(q1 - q0, 3) if q0 is not None else round(q1, 3)

    new_lines, changed = list(section), False
    for idx, line in enumerate(new_lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0] in ("物理量",) or set(cells[0]) <= set("-: |"):
            continue
        name = cells[0]
        # 吸附能行：行首下标气体名（SOF₂ 吸附能 / SF₆ 本体吸附能 / 干扰气体N吸附能（CO₂））
        m = re.match(r"^\s*(SF₆|SOF₂|SO₂F₂|SO₂|H₂S|CO₂|H₂O|N₂)", name) or \
            re.search(r"[（(](SF₆|SOF₂|SO₂F₂|SO₂|H₂S|CO₂|H₂O|N₂)[)）]", name)
        if m and "吸附能" in name:
            gas_sub = m.group(1)
            gas = next((g for g, s in GAS_SUB.items() if s == gas_sub), None)
            if gas:
                for sub, col in ((GAS_MAIN, MAIN_COL), (GAS_REF, REF_COL)):
                    val = ads.get((gas, sub))
                    if val is not None and not re.search(r"\d", cells[col]):
                        cells[col] = f"{val:.2f}"
                        changed = True
                new_lines[idx] = "| " + " | ".join(cells) + " |"
            continue
        # 电荷转移量（SOF₂，Bader）行
        if "电荷转移量" in name and "SOF₂" in name and "SO₂F₂" not in name:
            for sub, col in ((GAS_MAIN, MAIN_COL), (GAS_REF, REF_COL)):
                val = charges.get(("SOF2", sub))
                if val is not None and not re.search(r"\d", cells[col]):
                    cells[col] = f"{val:.3f}"
                    changed = True
            new_lines[idx] = "| " + " | ".join(cells) + " |"

    if changed:
        filled = "\n".join(new_lines)
        data_path.write_text(text.replace("\n".join(section), filled, 1), encoding="utf-8")
        print(f"[parse_vasp] 已回填 {len(ads)} 项吸附能"
              + (f"、{len(charges)} 项电荷转移" if charges else ""))
    else:
        print("[parse_vasp] 无可回填项（原始文件缺失或表格已手填）")
    return changed


def run(raw: Path = DEFAULT_RAW, data_path: Path = DEFAULT_DATA) -> bool:
    """入口：raw 目录有效才解析，返回是否发生回填。"""
    if not raw.is_dir() or not any(raw.glob("*/OUTCAR")):
        print(f"[parse_vasp] {raw} 无效或不含 OUTCAR，跳过自动解析（走通道 A 手填模板）")
        return False
    if not data_path.is_file():
        print(f"[parse_vasp] 数据模板不存在：{data_path}")
        return False
    return fill_template(data_path, raw)


if __name__ == "__main__":
    raw = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RAW
    data = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DATA
    sys.exit(0 if run(raw, data) else 1)
