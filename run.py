"""兼容旧入口：转发到 main.py（Flow 编排）。

用法（项目根目录）：
    & "..\.venv\Scripts\python.exe" run.py
等价于：
    & "..\.venv\Scripts\python.exe" main.py
"""

from main import main

if __name__ == "__main__":
    main()
