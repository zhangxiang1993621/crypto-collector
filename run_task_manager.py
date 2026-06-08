"""任务管理面板启动器
用法: python run_task_manager.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from task_manager.gui import main

if __name__ == "__main__":
    main()
