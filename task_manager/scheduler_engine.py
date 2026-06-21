"""任务调度引擎

基于 APScheduler 的本地调度器，读取配置并定时执行 Python 脚本。
"""

import os
import re
import sys
import json
import logging
import subprocess
import threading
import traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable

import yaml
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

logger = logging.getLogger("scheduler_engine")

PROJECT_DIR = Path(__file__).parent.parent

# 加载 .env，确保子进程能继承环境变量
load_dotenv(dotenv_path=PROJECT_DIR / ".env")

CONFIG_FILE = PROJECT_DIR / "task_config.json"
YAML_FILE = PROJECT_DIR / ".github" / "workflows" / "scheduler.yml"


# ──────────────── 配置管理 ────────────────

def import_from_yaml() -> list[dict]:
    """从 GitHub Actions YAML 导入任务配置"""
    if not YAML_FILE.exists():
        return []

    with open(YAML_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    tasks = []
    # on 在 YAML 中被解析为布尔值 True
    on_data = data.get("on") or data.get(True) or {}
    cron_list = on_data.get("schedule", [])
    jobs = data.get("jobs", {})

    # 按顺序配对 cron 和 job
    job_items = list(jobs.items())
    for i, (job_name, job_def) in enumerate(job_items):
        cron = cron_list[i]["cron"] if i < len(cron_list) else "0 0 * * *"

        # 提取 run 命令
        steps = job_def.get("steps", [])
        run_cmd = ""
        env_vars = {}
        for step in steps:
            if step.get("run"):
                run_cmd = step["run"]
            if step.get("env"):
                env_vars = step["env"]

        # 拆分复合命令
        commands = [c.strip() for c in run_cmd.split(";") if c.strip()] if run_cmd else []

        tasks.append({
            "name": job_name,
            "label": _job_label(job_name),
            "cron": cron,
            "enabled": False,
            "commands": commands,
            "env_vars": env_vars,
            "working_dir": str(PROJECT_DIR),
        })

    return tasks


def _job_label(job_name: str) -> str:
    labels = {
        "scrape_api": "通用抓取（API/httpx）",
        "scrape_browser": "浏览器抓取（Playwright）",
        "us_stock": "美股数据采集 & 分钟K线",
    }
    return labels.get(job_name, job_name)


def load_config() -> list[dict]:
    """加载任务配置，优先 JSON，否则从 YAML 导入，最后合并内置手动任务"""
    tasks = []
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            tasks = json.load(f)
    else:
        tasks = import_from_yaml()

    # 合并内置手动任务（确保不被遗漏）
    _merge_builtin_tasks(tasks)
    return tasks


def _merge_builtin_tasks(tasks: list[dict]) -> None:
    """将内置手动任务合并到配置中"""
    existing_names = {t["name"] for t in tasks}

    builtins = [
        {
            "name": "create_bots",
            "label": "批量创建机器人",
            "cron": "",
            "enabled": False,
            "trigger": "manual",
            "commands": ["python task_manager/create_bots.py"],
            "env_vars": {},
            "working_dir": str(PROJECT_DIR),
        },
        {
            "name": "clean_all",
            "label": "⚠️ 清空全部帖子和评论（测试用）",
            "cron": "",
            "enabled": False,
            "trigger": "manual",
            "commands": ["python tools/clean_all.py --yes"],
            "env_vars": {},
            "working_dir": str(PROJECT_DIR),
        },
    ]

    for bt in builtins:
        if bt["name"] not in existing_names:
            tasks.append(bt)
            logger.info(f"注册内置手动任务: {bt['label']}")


def save_config(tasks: list[dict]) -> None:
    """保存任务配置到 JSON"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    logger.info(f"配置已保存: {CONFIG_FILE}")


def sync_to_yaml(tasks: list[dict]) -> None:
    """同步配置回 GitHub Actions YAML"""
    if not YAML_FILE.exists():
        return

    with open(YAML_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    cron_list = [{"cron": t["cron"]} for t in tasks]
    # on 在 YAML 中被解析为布尔值 True
    on_data = data.get("on") or data.get(True) or {}
    on_data["schedule"] = cron_list

    # 写入临时内容
    raw = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    # PyYAML 将 on 转为 true，需要修正回 on
    raw = re.sub(r'^true:', 'on:', raw, flags=re.MULTILINE)

    with open(YAML_FILE, "w", encoding="utf-8") as f:
        f.write(raw)
    logger.info(f"已同步到 YAML: {YAML_FILE}")


# ──────────────── 调度引擎 ────────────────

class TaskScheduler:
    """本地任务调度器"""

    def __init__(self, log_callback: Callable[[str, str], None] | None = None):
        """
        log_callback: (task_name, message) 回调，用于 GUI 实时日志
        """
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.log_callback = log_callback
        self.job_status: dict[str, dict] = {}  # task_name → {last_run, last_status, next_run}

        self.scheduler.add_listener(self._on_job_done, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
        self._tasks: list[dict] = []

    def _on_job_done(self, event) -> None:
        task_name = event.job_id
        if task_name in self.job_status:
            self.job_status[task_name]["last_run"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if event.exception:
                self.job_status[task_name]["last_status"] = "失败"
            else:
                self.job_status[task_name]["last_status"] = "成功"

    def load_and_start(self) -> None:
        """加载配置并启动所有已启用的任务"""
        self._tasks = load_config()
        for task in self._tasks:
            self.job_status[task["name"]] = {
                "last_run": "-",
                "last_status": "-",
                "next_run": "-",
            }
            if task["enabled"]:
                self._add_job(task)

        if not self.scheduler.running:
            self.scheduler.start()
            self._log(None, "调度器已启动")

        self._update_next_runs()

    def _add_job(self, task: dict) -> None:
        """添加单个定时任务（手动任务仅记录状态，不加入调度器）"""
        if not task.get("cron"):
            self._log(task["name"], "手动触发任务，已就绪")
            return
        try:
            job = self.scheduler.add_job(
                func=self._execute_task,
                trigger=CronTrigger.from_crontab(task["cron"], timezone="UTC"),
                args=[task],
                id=task["name"],
                name=task["label"],
                replace_existing=True,
            )
            self._log(task["name"], f"已调度 (cron: {task['cron']})")
        except Exception as e:
            self._log(task["name"], f"调度失败: {e}")

    def _execute_task(self, task: dict) -> None:
        """在线程中执行任务（实时流式输出日志）"""
        self._log(task["name"], "▶ 开始执行")
        try:
            for cmd in task["commands"]:
                env = os.environ.copy()
                env.update(task.get("env_vars", {}))
                # 过滤 ${{ ... }} 占位符（GitHub Actions 语法）
                clean_env = {}
                for k, v in env.items():
                    if isinstance(v, str) and v.startswith("${{"):
                        var_name = v.strip("${{").strip("}}").strip().split(".")[-1]
                        real_val = os.environ.get(var_name, "")
                        # 空值不传递：让子进程使用 os.environ.get("VAR", default) 的默认值
                        if not real_val:
                            continue
                        clean_env[k] = real_val
                    else:
                        clean_env[k] = v

                self._log(task["name"], f"  运行: {cmd}")

                # 用当前解释器路径替换 python，确保子进程使用同一 conda 环境
                cmd = re.sub(r"\bpython\b", lambda _: sys.executable, cmd)

                # 使用 Popen 实时流式读取输出
                process = subprocess.Popen(
                    cmd,
                    shell=True,
                    cwd=task.get("working_dir", str(PROJECT_DIR)),
                    env=clean_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )

                for line in process.stdout:
                    line = line.rstrip("\n")
                    if line:
                        self._log(task["name"], f"    {line}"[:250])

                process.wait(timeout=600)

                if process.returncode != 0:
                    self._log(task["name"], f"✗ 命令失败 (exit={process.returncode})")
                    return
            self._log(task["name"], "✓ 执行完成")
        except subprocess.TimeoutExpired:
            self._log(task["name"], "✗ 执行超时(10分钟)")
        except Exception:
            self._log(task["name"], f"✗ 异常: {traceback.format_exc()[-300:]}")

    def _log(self, task_name: str | None, message: str) -> None:
        logger.info(f"[{task_name or '系统'}] {message}")
        if self.log_callback:
            self.log_callback(task_name or "系统", message)

    def _update_next_runs(self) -> None:
        for job in self.scheduler.get_jobs():
            name = job.id
            if name in self.job_status and job.next_run_time:
                self.job_status[name]["next_run"] = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")

    def get_status(self) -> dict:
        self._update_next_runs()
        return dict(self.job_status)

    def enable_task(self, task_name: str) -> None:
        for t in self._tasks:
            if t["name"] == task_name:
                t["enabled"] = True
                save_config(self._tasks)
                break
        # 仅当有 cron 且未在调度器中时才添加
        task = next((t for t in self._tasks if t["name"] == task_name), None)
        if task and task.get("cron") and not self.scheduler.get_job(task_name):
            self._add_job(task)
        self._update_next_runs()
        self._log(task_name, "已启用")

    def disable_task(self, task_name: str) -> None:
        for t in self._tasks:
            if t["name"] == task_name:
                t["enabled"] = False
                save_config(self._tasks)
                break
        try:
            self.scheduler.remove_job(task_name)
        except Exception:
            pass
        self._update_next_runs()
        self._log(task_name, "已停用")

    def update_cron(self, task_name: str, new_cron: str) -> bool:
        try:
            CronTrigger.from_crontab(new_cron)
        except ValueError as e:
            self._log(task_name, f"无效 cron 表达式: {e}")
            return False

        for t in self._tasks:
            if t["name"] == task_name:
                t["cron"] = new_cron
                save_config(self._tasks)
                break

        if self.scheduler.get_job(task_name):
            self.scheduler.reschedule_job(
                task_name, trigger=CronTrigger.from_crontab(new_cron, timezone="UTC")
            )
        self._update_next_runs()
        self._log(task_name, f"Cron 已更新: {new_cron}")
        return True

    def run_now(self, task_name: str) -> None:
        for t in self._tasks:
            if t["name"] == task_name:
                threading.Thread(target=self._execute_task, args=(t,), daemon=True).start()
                return

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            self._log(None, "调度器已停止")

    def get_tasks(self) -> list[dict]:
        return list(self._tasks)
