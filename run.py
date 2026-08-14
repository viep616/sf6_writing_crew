"""直接运行入口。

绕过 crewai CLI 的 uv 环境管理与交互式 TUI，
复用项目根目录（挑战杯/.venv）已装好的 crewai 环境直接运行 crew.jsonc。

用法（在本项目目录下）：
    & "..\\.venv\\Scripts\\python.exe" run.py
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 将 crewai 的运行数据目录（任务输出数据库等）重定向到项目内 .appdata/，
# 避免写入系统 AppData\Local\CrewAI（沙箱/权限受限环境下无法写入）。
# 注意：必须在首次 import crewai 之前完成 patch（下游模块会 from ... import 该函数）。
import crewai_core.paths as _crewai_paths

_RUN_DATA_DIR = str(BASE_DIR / ".appdata")
_crewai_paths.db_storage_path = lambda: _RUN_DATA_DIR
os.environ["APPDATA"] = _RUN_DATA_DIR
os.environ["LOCALAPPDATA"] = _RUN_DATA_DIR

from dotenv import load_dotenv

load_dotenv(BASE_DIR / ".env", override=True)

from crewai.project.crew_loader import load_crew


def main() -> None:
    crew, default_inputs = load_crew(BASE_DIR / "crew.jsonc")
    result = crew.kickoff(inputs=default_inputs)
    print("\n========== Crew 运行结束 ==========")
    print(result)


if __name__ == "__main__":
    main()
