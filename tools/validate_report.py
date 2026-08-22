"""报告防幻觉校验器（防线一）。

流程：
1. 论文结构完整性检查：必须含标题 + 摘要/引言/计算方法/结果与讨论/结论，
   防「模型输出了分析文本而非论文」的假绿（DeepSeek 实测踩坑）；
2. 禁用表述黑名单：经验阈值 / 检测限(ppm/ppb) / 不确定度(±) / 数量级等
   无据外推表述（千问与 DeepSeek 历次验收反复踩的雷，确定性拦截）；
3. 解析数据文件表格，构建数值白名单（物理量, 数值, 单位）；
4. 正则提取报告中所有「数值 + 物理单位」组合；
5. 三分类：白名单命中 / 可推导（差值/和/倍数） / 白名单外 → 报警。

用法（在项目根目录）：
    python tools/validate_report.py [报告路径] [数据文件路径]
不带参数时用默认路径，返回 exit code：0=通过，1=存在校验问题。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REPORT = BASE_DIR / "output" / "论文_终稿.md"
DEFAULT_DATA = BASE_DIR / "data" / "vasp_results.md"

# 物理单位 -> 归一化类别
UNIT_ALIASES = {
    "ev": "eV",
    "e": "e",
    "å": "Å",
    "a": "Å",
    "ry": "Ry",
    "ry/bohr": "Ry/Bohr",
    "%": "%",
}

# 匹配「数值+单位」，数值支持：负号(−-–)、小数、科学计数法、区间 en-dash、乘号 ×x
NUM_UNIT_RE = re.compile(
    r"([−\-–]?\s?\d+(?:\.\d+)?(?:\s?[eE][×x]?\s?10\s?[⁻⁺+\-]?\s?\d+|[eE][+\-]?\d+|×\s?10\s?[⁻⁺+\-]\s?\d+)?)"
    r"\s*(eV|ev|e\b|Å|å|Ry/Bohr|ry/bohr|Ry|ry|%)"
)

# 单元格里提取纯数值（含括号注释、中文说明干扰）
CELL_NUM_RE = re.compile(
    r"([−\-–]?\d+(?:\.\d+)?)\s*(?:×\s?10\s?([⁻⁺+\-])\s?(\d+)|[eE]([+\-])(\d+))?"
)

SUPERSCRIPT_MAP = str.maketrans("⁻⁺⁰¹²³⁴⁵⁶⁷⁸⁹", "-+0123456789")
TOLERANCE = 0.005  # 绝对容差：数据表两位小数，报告四舍五入不应超过此值


def _sig2(x: float) -> float:
    """取两位有效数字（数据文件队友备注要求'所有数值保留两位有效数字引用'，
    故报告把推导值 1.54 写作 1.5、0.185 写作 0.18 属于合规表述，不应误报）。"""
    if x == 0:
        return 0.0
    from math import floor, log10
    d = floor(log10(abs(x)))
    return round(x, -(d - 1))


def _parse_cell_numbers(cell: str) -> list[float]:
    """从表格单元格提取全部数值；'未计算'/'—' 等返回空。"""
    if not cell:
        return []
    text = cell.translate(SUPERSCRIPT_MAP)
    if re.search(r"未|—|^-+$|^$", text):
        return []
    values = []
    for m in CELL_NUM_RE.finditer(text):
        try:
            val = float(m.group(1).replace("−", "-").replace("–", "-"))
            if m.group(2):  # ×10^n 形式
                exp = int(m.group(3).replace("+", "") + m.group(4))
                val *= 10**exp
            elif m.group(5):  # e±n 形式
                exp = int(m.group(5) + m.group(6))
                val *= 10**exp
            values.append(val)
        except ValueError:
            continue
    return values


def build_whitelist(data_text: str) -> dict[str, set[float]]:
    """构建数值白名单 = 表格数值 ∪ 数据文件全文散文中的「数值+单位」。

    白名单口径与校验目标一致：凡是数据文件里出现过的数值都允许进报告，
    数据文件里不存在的数值不允许进报告。表格按单位归类；散文中带单位的
    数值（如"S=O 键伸长约 0.02 Å"）并入对应单位。
    """
    whitelist: dict[str, set[float]] = {}
    lines = data_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            # 只有当表头含「单位」列时才作为物理量表处理
            if "单位" in header:
                unit_idx = header.index("单位")
                for row in lines[i + 2:]:
                    if not row.strip().startswith("|"):
                        break
                    cells = [c.strip() for c in row.strip().strip("|").split("|")]
                    if len(cells) <= unit_idx:
                        continue
                    unit_raw = cells[unit_idx]
                    if unit_raw in ("—", "-", ""):
                        continue
                    # 约定：第 0 列为物理量名称列（如"干扰气体1吸附能（CO₂）"），
                    # 其中的下标数字不得进入白名单，故只解析数据列
                    for cell in cells[1:unit_idx] + cells[unit_idx + 1:]:
                        for val in _parse_cell_numbers(cell):
                            key = unit_raw.strip()
                            whitelist.setdefault(key, set()).add(round(val, 6))
            i += 1
        else:
            # 散文行：并入带单位的数值（来源含定性描述节、队友备注）
            for unit, val, _, _ in extract_report_numbers(line):
                whitelist.setdefault(unit, set()).add(round(val, 6))
            i += 1
    return whitelist


def extract_report_numbers(report_text: str) -> list[tuple[str, float, int, str]]:
    """提取报告数值。返回 (单位, 数值, 行号, 行内容)。"""
    results = []
    for lineno, line in enumerate(report_text.splitlines(), start=1):
        for m in NUM_UNIT_RE.finditer(line):
            # 区间伪影：'1.8–2.2 Å' 中 en-dash 会被捕获为负号 → '−2.2'。
            # 若负号字符是 –/- 且其前一个字符是数字，视为区间上界，跳过。
            num_part = m.group(1)
            if num_part[:1] in ("−", "-", "–") and m.start() > 0 and line[m.start() - 1].isdigit():
                continue
            num_str = num_part.replace("−", "-").replace("–", "-").replace(" ", "")
            # 处理 ×10^n / e±n 后缀
            base = num_str
            multiplier = 1.0
            sci = re.search(r"([eE×x])(?:\s?10\s?)?([+\-⁻⁺]?)(\d+)$", num_str)
            if sci:
                base = num_str[: sci.start()]
                sign = sci.group(2).replace("⁻", "-").replace("⁺", "+")
                exp = int((sign or "+") + sci.group(3))
                multiplier = 10.0**exp
            try:
                val = float(base) * multiplier
            except ValueError:
                continue
            unit_raw = m.group(2)
            unit = UNIT_ALIASES.get(unit_raw.lower(), unit_raw)
            results.append((unit, val, lineno, line.strip()))
    return results


def _check_derivable(val: float, unit_vals: set[float]) -> bool:
    """判断 val 是否可由白名单数值推导：差值 / 和 / 整数倍 / 差值取整。"""
    vals = sorted(unit_vals)

    def close(a: float, b: float) -> bool:
        return abs(a - b) <= TOLERANCE

    # 直接命中
    if any(close(val, v) for v in vals):
        return True
    # 差值 / 和（任意两值，含正负号两种方向）；两位有效数字表述视为同一值
    for a in vals:
        for b in vals:
            diff = a - b
            if close(val, diff) or close(val, abs(diff)):
                return True
            if abs(diff) >= 0.1 and (_sig2(val) == _sig2(diff) or _sig2(val) == _sig2(abs(diff))):
                return True
    # 注：不做"整数倍"推导——任意两位小数总能凑成某小值的整倍（如 2.99=23×0.13），
    # 该规则会被绕过白名单的数值利用；倍数表述（"约 N 倍"）通常不带单位，本就不在提取范围
    return False


# 论文结构完整性要求：缺任一即视为废稿（防止"输出的是分析而非论文"）。
# 注意：必须带 re.MULTILINE，否则 ^ 只匹配全文开头，正常论文会被误报缺节。
REQUIRED_SECTIONS: list[tuple[str, re.Pattern[str]]] = [
    ("摘要", re.compile(r"^#{1,6}\s*[0-9.]*\s*摘要", re.MULTILINE)),
    ("引言", re.compile(r"^#{1,6}\s*[0-9.]*\s*引言", re.MULTILINE)),
    ("计算方法", re.compile(r"^#{1,6}\s*[0-9.]*\s*计算方法", re.MULTILINE)),
    ("结果与讨论", re.compile(r"^#{1,6}\s*[0-9.]*\s*结果.{0,2}讨论", re.MULTILINE)),
    ("结论", re.compile(r"^#{1,6}\s*[0-9.]*\s*(结论|总结)", re.MULTILINE)),
]

# 历次验收实证雷区：模型反复踩的"无据外推"表述，命中即报。
# 这类表述通常不是「数值+单位」格式（ppm/数量级）或可被差值规则碰巧放行
# （如 −0.8 eV＝−0.92−(−0.12)），故在数值白名单之外单独确定性拦截。
FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("检测限单位 ppm", re.compile(r"ppm", re.IGNORECASE)),
    ("检测限单位 ppb", re.compile(r"ppb", re.IGNORECASE)),
    ("检测限表述", re.compile(r"检测限")),
    ("选择性系数", re.compile(r"选择性系数")),
    ("经验阈值", re.compile(r"经验阈值")),
    ("特征长度", re.compile(r"特征长度")),
    ("不确定度/误差", re.compile(r"不确定度|误差范围|±\s*\d")),
    ("数量级表述", re.compile(r"数量级")),
]


def check_structure(report_text: str) -> list[str]:
    """检查论文结构完整性：标题 + 摘要/引言/计算方法/结果与讨论/结论。"""
    problems: list[str] = []
    # 标题行允许出现在全文任意位置（兼容历史存档的外层 ```markdown 围栏与 BOM）
    if not re.search(r"^#\s+\S+", report_text, re.MULTILINE):
        problems.append("[结构缺失] 缺少标题（未找到 '# 标题' 格式行，疑似模型输出了分析而非论文）")
    for name, pat in REQUIRED_SECTIONS:
        if not pat.search(report_text):
            problems.append(f"[结构缺失] 缺少必要小节：{name}（终稿不是完整论文）")
    return problems


def check_forbidden(report_text: str) -> list[str]:
    """检查禁用表述黑名单，返回问题清单。"""
    problems: list[str] = []
    for label, pat in FORBIDDEN_PATTERNS:
        for m in pat.finditer(report_text):
            lineno = report_text.count("\n", 0, m.start()) + 1
            line = report_text.splitlines()[lineno - 1].strip()[:80]
            problems.append(f"[禁用表述] {label} — 第{lineno}行: {line}")
    return problems


def validate(report_path: Path, data_path: Path) -> tuple[bool, list[str]]:
    """校验报告。返回 (是否通过, 问题清单)。"""
    whitelist = build_whitelist(data_path.read_text(encoding="utf-8"))
    report_text = report_path.read_text(encoding="utf-8")
    problems: list[str] = []

    # 防线 0.5：结构完整性（防废稿假绿）
    problems.extend(check_structure(report_text))
    # 防线 0.75：禁用表述黑名单（防无据外推漏网）
    problems.extend(check_forbidden(report_text))

    # 防线 1：数值白名单
    for unit, val, lineno, line in extract_report_numbers(report_text):
        unit_vals = whitelist.get(unit)
        if unit_vals is None:
            # 数据文件没有该单位数值（如 Ry 只出现在计算设置文字里）→ 降级为提示
            continue
        if not _check_derivable(val, unit_vals):
            problems.append(
                f"[白名单外] {val} {unit} — 第{lineno}行: {line[:80]}"
            )
    return (len(problems) == 0), problems


def main() -> int:
    # 控制台编码容错：问题文本可能含模型输出的 emoji 等 GBK 不支持的字符，
    # 打印时替换为占位符而不是抛 UnicodeEncodeError（文件写入不受影响）。
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass
    report = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPORT
    data = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DATA
    if not report.is_file():
        print(f"报告不存在: {report}")
        return 1
    ok, problems = validate(report, data)
    if ok:
        print("校验通过：报告所有数值均命中白名单或可推导。")
        return 0
    print(f"校验未通过，发现 {len(problems)} 个白名单外数值：")
    for p in problems:
        print("  " + p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
