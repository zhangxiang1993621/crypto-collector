"""P0 修复回归测试（纯 Python，不连数据库）。

运行：
    conda run -n trae-agent python tests/test_p0_fixes.py
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
PASSES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSES.append(name)
        print(f"PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL  {name}  {detail}")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── T1: db_direct.existing_titles ─────────────────────────────
import db_direct


def _fake_execute_sql(sql, params=None, fetch=True):
    return [{"title": "Alpha"}, {"title": "Beta"}]


db_direct.execute_sql = _fake_execute_sql
check("T1 existing_titles 已实现", hasattr(db_direct, "existing_titles"))
if hasattr(db_direct, "existing_titles"):
    got = db_direct.existing_titles("cat-1", ["Alpha", "Beta", "Gamma", "", None])
    check("T1 existing_titles 只返回已存在项", got == {"Alpha", "Beta"}, repr(got))

# ── T2: esports fetch_article_detail 失败时初始化 tags ────────
class _BoomPage:
    def goto(self, *a, **k):
        raise RuntimeError("timeout")

    def wait_for_selector(self, *a, **k):
        raise RuntimeError("timeout")


import esports.esports_scraper as es  # noqa: E402

try:
    out = es.fetch_article_detail(_BoomPage(), {"url": "https://example.com/x"})
    check("T2 详情页失败返回 tags=[] 且不抛异常",
          out.get("tags") == [] and out.get("content") == "", repr(out))
except Exception as e:  # noqa: BLE001
    check("T2 详情页失败返回 tags=[] 且不抛异常", False, f"raised {type(e).__name__}: {e}")

# ── T3: goal 标题在 LIVE→FT 稳定 ──────────────────────────────
import sport.goal.goal_scraper as gs  # noqa: E402


def _m(status, a, b):
    return {
        "teamA": {"name": "Brazil"},
        "teamB": {"name": "France"},
        "status": status,
        "score": {"teamA": a, "teamB": b},
        "round": {"name": "Group A"},
    }


_live = gs.build_match_title(_m("LIVE", 1, 0))
_ft = gs.build_match_title(_m("RESULT", 3, 1))
check("T3 LIVE 与 RESULT 标题一致", _live == _ft, f"{_live!r} != {_ft!r}")

# ── T4: 面板破坏性任务防护 ────────────────────────────────────
import task_manager.scheduler_engine as se  # noqa: E402

check("T4 is_destructive 已定义", hasattr(se, "is_destructive"))
if hasattr(se, "is_destructive"):
    check("T4 clean_all 被判定为破坏性", se.is_destructive("clean_all") is True)
    check("T4 普通爬虫不破坏", se.is_destructive("pintu") is False)

tasks: list = []
se._merge_builtin_tasks(tasks)
clean = next((t for t in tasks if t["name"] == "clean_all"), None)
check("T4 clean_all 内置命令不含 --yes",
      bool(clean) and "--yes" not in " ".join(clean["commands"]),
      repr(clean and clean["commands"]))

# ── T5: clean_all 标签清理计数真实 ────────────────────────────
ca = load_module(ROOT / "tools" / "clean_all.py", "clean_all_p0")
ca.execute_sql = lambda *a, **k: [{"id": 1}, {"id": 2}, {"id": 3}]
try:
    got5 = ca.clear_all_tags()
except Exception as e:  # noqa: BLE001
    got5 = f"raised {type(e).__name__}: {e}"
check("T5 clear_all_tags 返回真实行数", got5 == 3, repr(got5))

# ── T6: 6 个新闻脚本接入 existing_titles ──────────────────────
for rel in [
    "news_scrapers/tribunbola/tribunbola_scraper.py",
    "news_scrapers/sport_detik/detik_scraper.py",
    "news_scrapers/bolasport/bolasport_scraper.py",
    "news_scrapers/indo_news/indo_news_scraper.py",
    "news_scrapers/dailysports/dailysports_scraper.py",
    "news_scrapers/mainbasket/mainbasket_scraper.py",
]:
    src = (ROOT / rel).read_text(encoding="utf-8")
    check(f"T6 {rel} 使用 existing_titles", "existing_titles" in src)

# ── T7: 分类批量执行跳过破坏性任务 ────────────────────────────
sched = se.TaskScheduler()
sched._tasks = [
    {"name": "clean_all", "category": "管理工具", "commands": ["x"]},
    {"name": "pintu", "category": "加密市场", "commands": ["x"]},
]
_logs: list = []
sched._log = lambda name, msg: _logs.append((name, msg))
_started: list = []
_OrigThread = se.threading.Thread


class _SpyThread:
    def __init__(self, *a, **k):
        _started.append(k.get("args", ()))

    def start(self):
        pass


se.threading.Thread = _SpyThread
try:
    sched.run_category("管理工具")
finally:
    se.threading.Thread = _OrigThread
check("T7 全为破坏性任务时不启动执行", len(_started) == 0, repr(_started))
check("T7 记录跳过日志", any("跳过" in m for _, m in _logs), repr(_logs))

# ── T8: workflow 不再屏蔽失败 ─────────────────────────────────
wf = (ROOT / ".github/workflows/scheduler.yml").read_text(encoding="utf-8")
check("T8 无 continue-on-error", "continue-on-error" not in wf)
check("T8 无 || echo 兜底", "|| echo" not in wf)

print()
print(f"== {len(PASSES)} passed, {len(FAILURES)} failed ==")
if FAILURES:
    print("FAILED: " + ", ".join(FAILURES))
    sys.exit(1)
