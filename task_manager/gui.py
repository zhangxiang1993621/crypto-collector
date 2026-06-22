"""任务管理 GUI 界面

基于 tkinter 的任务管理面板，可编辑定时任务、启停调度、手动触发。
用法: python -m task_manager.gui
"""

import os
import sys
import threading
from datetime import datetime
from pathlib import Path

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import tkinter as tk
from tkinter import ttk, messagebox

from task_manager.scheduler_engine import (
    TaskScheduler, load_config, save_config, sync_to_yaml,
    CATEGORY_ORDER, get_task_category,
)


class TaskManagerApp:
    """任务管理主窗口"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("定时任务管理面板")
        self.root.geometry("1040x680")
        self.root.minsize(880, 550)

        self.scheduler = TaskScheduler(log_callback=self._on_log)
        self._tasks: list[dict] = []

        self._build_ui()
        self._load_and_start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ──────────────── UI 构建 ────────────────

    def _build_ui(self) -> None:
        # 顶部工具栏
        toolbar = ttk.Frame(self.root, padding=5)
        toolbar.pack(fill=tk.X)

        ttk.Label(toolbar, text="定时任务管理", font=("Microsoft YaHei", 14, "bold")).pack(side=tk.LEFT, padx=5)

        self.btn_start = ttk.Button(toolbar, text="⏸ 停止调度", command=self._toggle_scheduler)
        self.btn_start.pack(side=tk.RIGHT, padx=5)

        self.btn_save_yaml = ttk.Button(toolbar, text="同步到 YAML", command=self._save_to_yaml)
        self.btn_save_yaml.pack(side=tk.RIGHT, padx=5)

        self.btn_reload = ttk.Button(toolbar, text="重载配置", command=self._reload_config)
        self.btn_reload.pack(side=tk.RIGHT, padx=5)

        # 状态栏
        self.status_bar = ttk.Frame(self.root, padding=3)
        self.status_bar.pack(fill=tk.X)
        self.lbl_status = ttk.Label(self.status_bar, text="● 运行中", foreground="green")
        self.lbl_status.pack(side=tk.LEFT, padx=10)

        # 任务表格区域（分类树）
        table_frame = ttk.LabelFrame(self.root, text="任务列表", padding=5)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=5)

        columns = ("label", "cron", "enabled", "last_run", "last_status", "next_run")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="tree headings",
            selectmode="browse",
            height=8,
        )

        self.tree.heading("#0", text="任务名 / 分类")
        self.tree.heading("label", text="描述")
        self.tree.heading("cron", text="Cron 表达式")
        self.tree.heading("enabled", text="状态")
        self.tree.heading("last_run", text="上次运行")
        self.tree.heading("last_status", text="结果")
        self.tree.heading("next_run", text="下次运行")

        self.tree.column("#0", width=160, minwidth=140)
        self.tree.column("label", width=200)
        self.tree.column("cron", width=100)
        self.tree.column("enabled", width=80, anchor=tk.CENTER)
        self.tree.column("last_run", width=140, anchor=tk.CENTER)
        self.tree.column("last_status", width=50, anchor=tk.CENTER)
        self.tree.column("next_run", width=140, anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<Double-1>", self._on_double_click)

        # 操作按钮行
        btn_frame = ttk.Frame(self.root, padding=5)
        btn_frame.pack(fill=tk.X, padx=8)

        self.btn_run_category = ttk.Button(btn_frame, text="执行分类全部", command=self._run_category)
        self.btn_run_category.pack(side=tk.LEFT, padx=3)

        ttk.Button(btn_frame, text="编辑 Cron", command=self._on_edit_cron).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="启用 / 停用", command=self._toggle_task).pack(side=tk.LEFT, padx=3)
        self.btn_run_one = ttk.Button(btn_frame, text="立即执行", command=self._run_now)
        self.btn_run_one.pack(side=tk.LEFT, padx=3)

        # 日志区域
        log_frame = ttk.LabelFrame(self.root, text="运行日志", padding=3)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.log_text = tk.Text(log_frame, height=8, font=("Consolas", 9), wrap=tk.WORD, state=tk.DISABLED)
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 日志颜色标签
        self.log_text.tag_config("INFO", foreground="black")
        self.log_text.tag_config("SUCCESS", foreground="green")
        self.log_text.tag_config("ERROR", foreground="red")
        self.log_text.tag_config("SYSTEM", foreground="blue")

    # ──────────────── 数据加载 ────────────────

    def _load_and_start(self) -> None:
        self._tasks = load_config()
        self.scheduler.load_and_start()
        self._refresh_table()

    def _refresh_table(self) -> None:
        """刷新分类树"""
        status_map = self.scheduler.get_status()

        # 按分类组织任务
        grouped: dict[str, list[dict]] = {}
        for t in self._tasks:
            cat = t.get("category", "其他")
            if cat not in grouped:
                grouped[cat] = []
            grouped[cat].append(t)

        for cat_name in CATEGORY_ORDER:
            if cat_name not in grouped:
                grouped[cat_name] = []

        # 更新或创建分类节点
        for cat_name in reversed(CATEGORY_ORDER):
            cat_tasks = grouped.get(cat_name, [])
            enabled_count = sum(1 for t in cat_tasks if t["enabled"])
            total_count = len(cat_tasks)

            if self.tree.exists(cat_name):
                # 更新现有分类节点
                self.tree.item(cat_name, values=(
                    f"{enabled_count}/{total_count} 个任务",
                    "",
                    f"已启用 {enabled_count}" if enabled_count else "全部停用",
                    "", "", ""
                ), open=self.tree.item(cat_name, "open") if self.tree.get_children(cat_name) else True)
            else:
                # 按树顺序插入（新分类放最前面）
                existing = list(self.tree.get_children(""))
                insert_idx = 0
                for i, eid in enumerate(existing):
                    try:
                        if CATEGORY_ORDER.index(eid) > CATEGORY_ORDER.index(cat_name):
                            insert_idx = i + 1
                    except ValueError:
                        pass
                self.tree.insert("", insert_idx, iid=cat_name, text=f"📁 {cat_name}", values=(
                    f"{enabled_count}/{total_count} 个任务",
                    "",
                    f"已启用 {enabled_count}" if enabled_count else "全部停用",
                    "", "", ""
                ), open=True, tags=("category",))

        # 更新任务子节点（仅变更检测，避免闪烁）
        for cat_name in CATEGORY_ORDER:
            cat_tasks = grouped.get(cat_name, [])
            existing_children = set(self.tree.get_children(cat_name))
            config_names = {t["name"] for t in cat_tasks}

            # 删除不再存在的任务
            for child in existing_children - config_names:
                self.tree.delete(child)

            # 更新或新增任务
            for t in cat_tasks:
                name = t["name"]
                st = status_map.get(name, {})
                vals = (
                    t["label"],
                    t["cron"],
                    "已启用" if t["enabled"] else "已停用",
                    st.get("last_run", "-"),
                    st.get("last_status", "-"),
                    st.get("next_run", "-") if t["enabled"] else "-",
                )
                if self.tree.exists(name):
                    self.tree.item(name, values=vals)
                else:
                    self.tree.insert(cat_name, tk.END, iid=name, text=name, values=vals, tags=("task",))

        # 每5秒自动刷新
        self.root.after(5000, self._refresh_table)

    # ──────────────── 辅助方法 ────────────────

    def _is_category_selected(self) -> bool:
        """判断当前选中项是否为分类节点"""
        sel = self.tree.selection()
        if not sel:
            return False
        iid = sel[0]
        return iid in CATEGORY_ORDER

    def _get_selected_task(self) -> dict | None:
        """获取当前选中的任务（跳过分类节点）"""
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        if iid in CATEGORY_ORDER:
            return None
        return next((t for t in self._tasks if t["name"] == iid), None)

    # ──────────────── 操作 ────────────────

    def _toggle_scheduler(self) -> None:
        if self.scheduler.scheduler.running:
            self.scheduler.shutdown()
            self.btn_start.config(text="▶ 启动调度")
            self.lbl_status.config(text="● 已停止", foreground="red")
        else:
            self.scheduler = TaskScheduler(log_callback=self._on_log)
            self.scheduler.load_and_start()
            self.btn_start.config(text="⏸ 停止调度")
            self.lbl_status.config(text="● 运行中", foreground="green")

    def _on_double_click(self, event=None) -> None:
        """双击分类：展开/收起；双击任务：编辑 Cron"""
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid in CATEGORY_ORDER:
            # 分类节点：切换展开/收起
            if self.tree.item(iid, "open"):
                self.tree.item(iid, open=False)
            else:
                self.tree.item(iid, open=True)
        else:
            self._on_edit_cron()

    def _run_category(self) -> None:
        """执行选中分类下的全部任务"""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个分类")
            return
        iid = sel[0]
        if iid not in CATEGORY_ORDER:
            # 选中了任务，找到其所属分类
            task = self._get_selected_task()
            if task:
                iid = task.get("category", "")
            else:
                messagebox.showwarning("提示", "请选择一个分类节点")
                return

        cat_tasks = [t for t in self._tasks if t.get("category") == iid]
        if not cat_tasks:
            messagebox.showinfo("提示", f"分类「{iid}」下没有任务")
            return

        if not messagebox.askyesno("确认执行", f"即将执行 [{iid}] 下的全部 {len(cat_tasks)} 个任务，是否继续？"):
            return

        self._log_callback("系统", f"▶ 手动触发 [{iid}] 全部任务")
        self.scheduler.run_category(iid)

    def _toggle_task(self) -> None:
        task = self._get_selected_task()
        if not task:
            messagebox.showwarning("提示", "请先选择一个具体任务（不是分类）")
            return
        name = task["name"]

        if task["enabled"]:
            self.scheduler.disable_task(name)
        else:
            self.scheduler.enable_task(name)
        self._tasks = self.scheduler.get_tasks()

    def _on_edit_cron(self, event=None) -> None:
        task = self._get_selected_task()
        if not task:
            messagebox.showwarning("提示", "请先选择一个具体任务（不是分类）")
            return
        name = task["name"]

        dialog = CronEditDialog(self.root, task["label"], task["cron"])
        self.root.wait_window(dialog)
        if dialog.result:
            if self.scheduler.update_cron(name, dialog.result):
                self._tasks = self.scheduler.get_tasks()
                messagebox.showinfo("成功", f"已更新 {task['label']} 的 Cron 为: {dialog.result}")

    def _run_now(self) -> None:
        task = self._get_selected_task()
        if not task:
            messagebox.showwarning("提示", "请先选择一个具体任务（不是分类）")
            return
        name = task["name"]

        self._log_callback(name, "▶ 手动触发")
        self.scheduler.run_now(name)

    def _reload_config(self) -> None:
        self.scheduler.shutdown()
        self.scheduler = TaskScheduler(log_callback=self._on_log)
        self._load_and_start()
        self._log_callback("系统", "配置已重载")

    def _save_to_yaml(self) -> None:
        try:
            sync_to_yaml(self._tasks)
            messagebox.showinfo("成功", "已同步到 scheduler.yml")
        except Exception as e:
            messagebox.showerror("错误", f"同步失败: {e}")

    # ──────────────── 日志 ────────────────

    def _on_log(self, task_name: str, message: str) -> None:
        self.root.after(0, self._log_callback, task_name, message)

    def _log_callback(self, task_name: str, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{task_name}] {message}\n"

        if "✓" in message or "成功" in message:
            tag = "SUCCESS"
        elif "✗" in message or "失败" in message or "异常" in message or "Error" in message:
            tag = "ERROR"
        elif task_name == "系统":
            tag = "SYSTEM"
        else:
            tag = "INFO"

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line, tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        self.scheduler.shutdown()
        self.root.destroy()


class CronEditDialog(tk.Toplevel):
    """Cron 表达式编辑弹窗"""

    def __init__(self, parent: tk.Tk, label: str, current_cron: str):
        super().__init__(parent)
        self.title(f"编辑 Cron - {label}")
        self.geometry("500x350")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: str | None = None

        self._build(current_cron)

    def _build(self, current_cron: str) -> None:
        ttk.Label(self, text="Cron 表达式 (分 时 日 月 周)", font=("Microsoft YaHei", 10)).pack(pady=(15, 5))

        self.cron_var = tk.StringVar(value=current_cron)
        self.cron_entry = ttk.Entry(self, textvariable=self.cron_var, font=("Consolas", 13), width=30)
        self.cron_entry.pack(pady=5)

        # 快捷预设
        preset_frame = ttk.LabelFrame(self, text="快捷预设", padding=8)
        preset_frame.pack(pady=10, padx=20, fill=tk.X)

        presets = [
            ("每30分钟", "*/30 * * * *"),
            ("每1小时", "0 * * * *"),
            ("每2小时", "0 */2 * * *"),
            ("每6小时", "0 */6 * * *"),
            ("每天8:00", "0 8 * * *"),
            ("每天12:00", "0 12 * * *"),
            ("每天20:00", "0 20 * * *"),
            ("每周一8:00", "0 8 * * 1"),
        ]

        row = 0
        col = 0
        for label, cron in presets:
            ttk.Button(
                preset_frame, text=label,
                command=lambda c=cron: self.cron_var.set(c)
            ).grid(row=row, column=col, padx=3, pady=3, sticky="ew")
            col += 1
            if col >= 4:
                col = 0
                row += 1

        # 说明
        info = (
            "格式：分 时 日 月 周\n"
            "  * 表示任意  */N 表示每N\n"
            "  周: 0=周日 1=周一 ... 6=周六\n"
            "示例: 0 8 * * * = 每天8:00  */30 * * * * = 每30分钟"
        )
        ttk.Label(self, text=info, font=("Consolas", 8), foreground="gray").pack(pady=10)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="确定", command=self._on_ok).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="取消", command=self.destroy).pack(side=tk.LEFT, padx=10)

    def _on_ok(self) -> None:
        cron = self.cron_var.get().strip()
        from apscheduler.triggers.cron import CronTrigger
        try:
            CronTrigger.from_crontab(cron)
            self.result = cron
            self.destroy()
        except ValueError as e:
            messagebox.showerror("无效表达式", str(e), parent=self)


def main():
    root = tk.Tk()
    app = TaskManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
