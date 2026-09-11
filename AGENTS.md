<!-- BEGIN superpowers-dsh -->
<!-- 本段由 superpowers-dsh-install skill 自动安装/更新；标记内的内容在下次安装时会被整体替换 -->

# Superpowers for DSH

**ATTENTION AI:** This project uses the Superpowers Agentic Framework adapted for DSH (DeepSeek Harness). Runtime skills live in `.dsh/skills/` — DSH lists every skill (name + summary) in the session catalog automatically; load full instructions with the `skill` tool before acting. Subagent prompt templates live in `.dsh/agents/`. This AGENTS.md file is injected into every session and replaces the Trae SessionStart / UserPromptSubmit hooks.

First action in every new session, before any task work: load the `using-superpowers` skill via the `skill` tool. If it is missing from the catalog, read `.dsh/skills/using-superpowers/SKILL.md` directly and check the installation with `.dsh/scripts/validate-install.ps1`.

## 1. Instruction Priority

1. User instructions, repository instructions, and direct requests are highest priority.
2. Superpowers skills define how to perform engineering work.
3. Default model habits are lowest priority.

If a user instruction conflicts with a Superpowers skill, follow the user and state the conflict.

## 2. Iron Laws

- **No fix without root cause:** for bugs, test failures, or unexpected behavior, use `systematic-debugging` before proposing fixes.
- **No production code without a red test:** before implementation, use `test-driven-development`.
- **No blind mock assertions:** when tests involve mocks, use `testing-anti-patterns`.
- **No success claim without evidence:** before saying work is done, fixed, passing, installed, or updated, use `verification-before-completion` and verify real command output or observable state.

## 3. DSH Tool Mapping

Translate upstream Superpowers tool names to DSH native tools (full table: `.dsh/skills/using-superpowers/references/dsh-tools.md`):

| Upstream wording | DSH action |
|---|---|
| `Skill` tool, `superpowers:<skill>` | `skill` tool: `skill({ name: "<skill-name>" })` with the exact catalog name |
| `TodoWrite` | `todo_write` (always sends the ENTIRE list; it replaces the previous one) |
| `Task tool (general-purpose)` | `subagent` tool with a complete self-contained prompt (background by default; `run_in_background: false` only when the next action depends on the result) |
| Named subagents (implementer / reviewer) | Read the matching `.dsh/agents/*.md` prompt template, complete it, and pass it as the `subagent` prompt |
| Follow-up work building on this conversation | `subagent_fork` (inherits completed turns) |
| Multi-agent fan-out orchestration | `workflow` tool |
| `Read`, `Write`, `Edit` | `read` / `write` / `edit` (plus `glob`, `grep`) |
| `Bash` | `pwsh` (PowerShell; long-running work via `run_in_background` + `job_output` / `job_kill`) |
| `CLAUDE.md` | `AGENTS.md` (this file) |
| Session hooks (SessionStart / UserPromptSubmit) | Native: this file is injected every session and is visible on every turn |

Do not use legacy `find-skills`, `skill-run`, or `remembering-conversations` scripts. Skill discovery is DSH-native: the catalog is always visible; loading is an explicit `skill` tool call.

## 4. Subagent Selection

When delegating work, the candidate pool is DSH's built-in delegation tools (`subagent` = fresh self-contained context, `subagent_fork` = inherits this conversation, `workflow` = scripted fan-out) plus the named role templates in `.dsh/agents/`. Choose the strongest option for the current development need.

Strength means:

1. The template/agent description covers more of the user's actual goal.
2. It is more specialized for the current phase: implementation, task review, code review, plan review, debugging, research, or verification.
3. It carries the right workflow obligations, including tests, evidence, read-only review, or report format.
4. It avoids irrelevant scope and does not require hidden context — a DSH `subagent` does NOT see this conversation, so its prompt must contain every file path, constraint, and expected evidence.

Prefer these `.dsh/agents/` templates when their role matches exactly:

- `superpowers-implementer.md` for task-scoped implementation from a brief.
- `superpowers-task-reviewer.md` for reviewing one implemented task against its brief and report.
- `superpowers-code-reviewer.md` for broad review after a completed feature or before merge.
- `superpowers-plan-reviewer.md` for reviewing an implementation plan before execution.

Prefer `subagent_fork` for short follow-ups that genuinely need this conversation's context. If no subagent is clearly stronger, work inline and state why.

Do not report `.dsh/agents` or a named template as missing unless you have listed `.dsh/agents` from the current target root in the same turn. Treat any dispatch failure as a task/runtime failure and choose the next strongest option or continue inline.

## 5. Mandatory Skill Triggers

Use the matching skill before responding or acting. Load it with the `skill` tool; if the tool is unavailable, read `.dsh/skills/<skill>/SKILL.md` directly.

### Session Start

| Situation | Required skill |
|---|---|
| Starting a new conversation or project task | `using-superpowers` |

### Architecture and Planning

| Situation | Required skill |
|---|---|
| New feature, rewrite, refactor, UI, behavior change, or project idea | `brainstorming` |
| A spec or requirements need an implementation plan | `writing-plans` |
| Need isolated work before implementation | `using-git-worktrees` |
| Stuck on complexity, assumptions, scale, or approach | `when-stuck` |

### Problem-Solving Additions

| Situation | Required skill |
|---|---|
| Conventional approaches feel inadequate and unrelated analogies may unlock options | `collision-zone-thinking` |
| Hidden assumptions need to be flipped or challenged | `inversion-exercise` |
| The same pattern appears across multiple domains | `meta-pattern-recognition` |
| Two valid approaches optimize for different priorities | `preserving-productive-tensions` |
| Scale, limits, or edge cases need stress testing | `scale-game` |
| Complexity is growing through repeated special cases | `simplification-cascades` |
| A technical choice needs historical or lineage context | `tracing-knowledge-lineages` |

### Implementation and Review

| Situation | Required skill |
|---|---|
| Executing an implementation plan with independent tasks | `subagent-driven-development` |
| Executing a plan inline or when subagents are unavailable | `executing-plans` |
| Before first line of production code | `test-driven-development` |
| Writing or changing tests with mocks/test doubles | `testing-anti-patterns` |
| Completing a major task or before merge/PR | `requesting-code-review` |
| Receiving review feedback | `receiving-code-review` |

### Debugging and Completion

| Situation | Required skill |
|---|---|
| Bug, failing test, crash, or unexpected behavior | `systematic-debugging` |
| Symptom appears deep in a stack and origin is unclear | `root-cause-tracing` |
| Async test uses `sleep`, `setTimeout`, polling guesses, or is flaky | `condition-based-waiting` |
| Root cause is found and validation should prevent recurrence | `defense-in-depth` |
| About to claim done/fixed/passing/installed/updated | `verification-before-completion` |
| Implementation is complete and branch/worktree needs finishing | `finishing-a-development-branch` |

### Skill Maintenance

| Situation | Required skill |
|---|---|
| Creating, editing, migrating, or testing skills | `writing-skills` |
| Testing skill behavior with pressure scenarios | `testing-skills-with-subagents` |

## 6. Flattened Skill Compatibility

Upstream Superpowers v5 keeps several techniques as reference files inside parent skills. This package exposes the important ones as flat skills so trigger matching stays reliable:

- `condition-based-waiting`
- `defense-in-depth`
- `root-cause-tracing`
- `testing-anti-patterns`
- `testing-skills-with-subagents`

If a scenario matches one of these, load the flat skill directly.

## 7. Required Task Tracking

When a skill contains a checklist, phase list, graph, or multi-step process, the first action after loading it is to create `todo_write` items for those steps. Remember DSH's `todo_write` replaces the whole list on every call: always resend the complete list, keep exactly the items being worked on `in_progress`, and mark items `completed` as work actually completes.

## 8. Rule Reinforcement (DSH-native)

This AGENTS.md file is the persistent Superpowers reinforcement layer. DSH has no hooks.json; the platform provides the equivalents natively:

- Trae `SessionStart` (inject `using-superpowers`) → this file is injected at every session start and mandates loading `using-superpowers` first.
- Trae `UserPromptSubmit` (per-turn reminder) → this file is part of the system prompt and visible on every turn.
- Skill awareness → the session catalog (names + summaries) is always present; no hook needed.

Per-turn contract:

- For bugs, failed tests, crashes, and unexpected behavior, use `systematic-debugging` before proposing or applying a fix.
- For deep call-stack symptoms or unclear origin, use `root-cause-tracing`.
- For flaky async waits, sleeps, timeouts, or polling guesses, use `condition-based-waiting`.
- Before production code, use `test-driven-development`.
- Before claiming done, fixed, passing, installed, or updated, use `verification-before-completion` and cite real evidence.
- Choose the strongest subagent option and always pass complete task prompts, file paths, constraints, and expected evidence.
- Track multi-step skill workflows with `todo_write`.

## 9. Runtime Contract

- **Rule:** this AGENTS.md marker section defines non-negotiable trigger constraints.
- **Skills:** `.dsh/skills/*/SKILL.md` contain the actual workflow instructions, loaded via the `skill` tool.
- **Agents:** `.dsh/agents/*.md` are subagent prompt templates referenced by controller skills.
- **Env:** `.dsh/env.json` records the project's conda environment (see Python 规则 below).
- **Requests:** `.dsh/REQUESTS.md` collects wished-for skills for periodic review.
- **Validation:** `.dsh/scripts/validate-install.ps1` verifies installation completeness.

During installation or upgrade, never delete `.dsh/skills/`, `.dsh/agents/`, or the AGENTS.md marker section. These are runtime files, not removable residue.

## 10. Anti-Rationalization Checks

If any of these thoughts appear, stop and use the relevant skill:

- "This is too small for a workflow."
- "I need to inspect files first."
- "I already know what this skill says."
- "I'll add tests after the code works."
- "The test failure is obvious."
- "Manual verification is enough."
- "The user asked for speed, so I can skip review."

---

# 中文回答规则

- **所有回复必须使用简体中文**，包括解释、建议、错误分析等
- 代码注释使用简体中文（除非用户明确要求使用其他语言）
- 变量名、函数名等代码标识符遵循项目既有规范，不受此规则约束
- 代码中的字符串内容根据业务需要决定语言，不受此规则约束

# Python 虚拟环境使用规范

## CRITICAL: 强制执行规则

**任何 Python 命令前必须使用 `conda run -n <环境名>`，绝不使用裸 `python` 或 `pip`！**

## 核心工作流程

### 第一步：确认虚拟环境

1. 优先读取项目 `.dsh/env.json` 中的 `python_env` 值
2. 若文件不存在或值为空，运行 `conda env list` 查询可用环境
3. 向用户确认要使用的环境名
4. 将用户选择写入 `.dsh/env.json` 保存

### 第二步：执行 Python 命令

**正确方式（必须使用）：**
```powershell
conda run -n <环境名> python script.py
```

**错误方式（绝对禁止）：**
```powershell
python script.py
conda activate <环境名>; python script.py
```

### 第三步：安装依赖包

- **规则1**：安装前必须检查是否已安装：`conda run -n <环境名> pip show <包名>`；输出含 `Name:` 和 `Version:` → 已安装，跳过
- **规则2**：多个包用批量检查：`conda run -n <环境名> pip show <包1> <包2> <包3>`
- **规则3**：优先使用 requirements.txt：`conda run -n <环境名> pip install -r requirements.txt --quiet`（pip 自动跳过已满足版本的包）

## 执行前自检清单

**输出任何 python/pip 命令前，必须确认：**

1. 命令是否包含 `conda run -n <环境名>`？
2. 安装包前是否执行了 `pip show` 检查？
3. 环境名是否已通过 `.dsh/env.json` 或用户确认？

**违反以上任一检查，禁止执行命令！**

## 注意事项

- 用户明确指定环境时，直接使用并更新 `.dsh/env.json`
- 若项目有 `.python-version` 或 `pyproject.toml`，可辅助推断环境
- 定时任务从 `.dsh/env.json` 读取环境，无需询问

<!-- END superpowers-dsh -->
