---
name: using-superpowers
description: Use when starting any conversation or task in DSH - establishes mandatory skill activation, the DSH tool mapping, subagent selection, and workflow priority before any response or action
whenToUse: 每个新会话/新任务的第一步；或当你不确定该用哪个 Superpowers skill、如何派发 subagent、如何映射上游工具名时。
---

<SUBAGENT-STOP>
If you were dispatched through the DSH `subagent` tool for a specific task, skip this skill unless the task explicitly asks you to use it.
</SUBAGENT-STOP>

<EXTREMELY-IMPORTANT>
If you think there is even a 1% chance a skill might apply to what you are doing, you MUST load and use that skill before responding or acting.

DSH lists every installed skill (name + summary) in the session catalog. When a catalog entry matches, call the `skill` tool with its exact name to load the full instructions. If the skill tool is unavailable, read `.dsh/skills/<skill>/SKILL.md` directly.

This is not negotiable. You cannot rationalize your way out of this.
</EXTREMELY-IMPORTANT>

# Using Superpowers in DSH

## Runtime Bootstrap

DSH has no hooks.json. The runtime contract is carried by platform-native mechanisms:

| Layer | Location | Purpose |
|---|---|---|
| Rule | `AGENTS.md` (marker section `superpowers-dsh`) | injected every session; keeps mandatory triggers visible; replaces Trae SessionStart / UserPromptSubmit hooks |
| Catalog | session system prompt | every skill's name + summary is always visible; no auto-loading of full bodies |
| Skill | `.dsh/skills/*/SKILL.md` | the actual workflow instructions, loaded via the `skill` tool |
| Agent templates | `.dsh/agents/*.md` | named subagent prompt templates for common dispatch roles |
| Env | `.dsh/env.json` | project conda environment for Python rules |

First action in a new session: load this skill. If these layers disagree, prefer direct user instructions first, then AGENTS.md rules, then the current skill content.

## Instruction Priority

1. User's explicit instructions, project rules, and direct requests are highest priority.
2. Superpowers skills define the required workflow and override casual default behavior.
3. Default model habits are lowest priority.

If a user instruction conflicts with a skill, follow the user and state the conflict.

## DSH Tool Mapping

When upstream Superpowers text mentions another harness, translate it to DSH (full table: `references/dsh-tools.md`):

| Upstream wording | In DSH |
|---|---|
| `Skill` tool, `superpowers:<name>` | `skill` tool: `skill({ name: "<skill-name>" })` |
| `TodoWrite` | `todo_write` (always resends the ENTIRE list) |
| `Task tool (general-purpose)` | `subagent` with the completed self-contained prompt template |
| Named subagent (implementer/reviewer) | read the matching `.dsh/agents/*.md` template, complete it, pass as `subagent` prompt |
| Follow-up needing this conversation | `subagent_fork` |
| Multi-agent fan-out | `workflow` tool |
| `Read`, `Write`, `Edit` | `read` / `write` / `edit` (plus `glob`, `grep`) |
| `Bash` | `pwsh` (background: `run_in_background` + `job_output` / `job_kill`) |
| local conversation memory scripts | not used in this package |

Do not use legacy `find-skills`, `skill-run`, or `remembering-conversations` scripts. Skill discovery and activation are DSH-native.

## Subagent Selection

When delegating work, choose from the full pool: DSH built-in delegation (`subagent` fresh context / `subagent_fork` inherited context / `workflow` fan-out) plus the named role templates in `.dsh/agents/`. Pick the strongest option for the current development need.

Strongest means the best fit across:

- Goal coverage: its description covers the user's actual requested work.
- Phase specialization: implementation, task review, code review, plan review, debugging, research, or verification.
- Workflow obligations: tests, evidence, read-only review, report format, or constraints.
- Scope control: a DSH `subagent` does NOT see this conversation — the prompt must be self-contained with every path, constraint, and expected evidence.

Use these templates when they match exactly:

- `superpowers-implementer.md` for task-scoped implementation from a brief.
- `superpowers-task-reviewer.md` for one-task review against a brief, report, and diff package.
- `superpowers-code-reviewer.md` for broad code review after a completed feature or before merge.
- `superpowers-plan-reviewer.md` for implementation-plan review before execution.

If no subagent is clearly stronger, work inline and state why. Do not claim `.dsh/agents` is missing unless you listed it from the current target root in the same turn.

## The Rule

Use relevant or requested skills before any response, clarification, file read, shell command, implementation, or status claim.

Before entering plan mode or implementation planning: if you have not already brainstormed the work, use `brainstorming` first.

If you use a skill:

1. Announce briefly: "I'm using `<skill>` to `<purpose>`."
2. If the skill has a checklist or multi-step process, create `todo_write` items for the steps (resend the whole list on every update).
3. Follow the skill exactly unless the user explicitly overrides it.

If a skill contains prompt templates such as `implementer-prompt.md` or `code-reviewer.md`, load the template, complete it, and pass the full text as the `subagent` prompt:

- `superpowers-implementer` for `subagent-driven-development/implementer-prompt.md`
- `superpowers-task-reviewer` for `subagent-driven-development/task-reviewer-prompt.md`
- `superpowers-code-reviewer` for `requesting-code-review/code-reviewer.md`
- `superpowers-plan-reviewer` for `writing-plans/plan-document-reviewer-prompt.md`

Do not rely on session history as a substitute for the template or referenced files.

## Skill Priority

Use process skills before implementation skills.

| Situation | First skill |
|---|---|
| New feature, build, rewrite, behavior change | `brainstorming` |
| Written spec or requirements need an implementation plan | `writing-plans` |
| Executing a plan with independent tasks | `subagent-driven-development` |
| Executing a plan inline or without subagents | `executing-plans` |
| Starting implementation work | `test-driven-development` |
| Bug, test failure, or unexpected behavior | `systematic-debugging` |
| Deep symptom with unclear original cause | `root-cause-tracing` |
| Flaky async tests or sleeps/timeouts | `condition-based-waiting` |
| Before claiming done/fixed/passing | `verification-before-completion` |
| Before merge, PR, or major handoff | `requesting-code-review` |
| Completing branch/worktree workflow | `finishing-a-development-branch` |
| Writing or updating skills | `writing-skills` |
| Stuck and unsure which problem-solving approach fits | `when-stuck` |

### Local Problem-Solving Additions

| Situation | Skill |
|---|---|
| Force unrelated concepts together for breakthrough options | `collision-zone-thinking` |
| Flip assumptions to reveal hidden constraints | `inversion-exercise` |
| Recognize repeated patterns across domains | `meta-pattern-recognition` |
| Preserve two valid approaches without premature collapse | `preserving-productive-tensions` |
| Stress-test architecture or decisions at extreme scale | `scale-game` |
| Remove multiple components with one simplifying insight | `simplification-cascades` |
| Trace why an idea or technical choice evolved | `tracing-knowledge-lineages` |

Examples:

- "Let's build X" -> `brainstorming` first, then implementation/domain skills.
- "Fix this bug" -> `systematic-debugging` first, then domain skills.

## Flattened Skills

Upstream Superpowers v5 keeps some techniques as reference files inside parent skills. This package also exposes the most important references as first-class skills so they can trigger directly:

- `condition-based-waiting`
- `defense-in-depth`
- `root-cause-tracing`
- `testing-anti-patterns`
- `testing-skills-with-subagents`

Use the flat skill name when the scenario matches, even if the parent skill also links to the same material.

## Red Flags

These thoughts mean stop and use the relevant skill:

| Thought | Reality |
|---|---|
| "This is just a simple question" | Questions are tasks. Check skills first. |
| "I need more context first" | Skill check comes before context gathering. |
| "Let me inspect files quickly" | Skills define how to inspect. |
| "I can check git/files quickly" | Files lack conversation context. Check skills first. |
| "Let me gather information first" | Skills tell you how to gather information. |
| "This doesn't need a formal skill" | If a skill exists, use it. |
| "I remember this skill" | Skills evolve. Load the current one. |
| "This doesn't count as a task" | Action equals task. Check skills first. |
| "The skill is overkill" | Simple things become complex. Use it. |
| "I'll just do this one thing first" | Check before doing anything. |
| "This feels productive" | Undisciplined action wastes time. Skills prevent this. |
| "I know what that means" | Knowing the concept is not the same as using the skill. Use it. |
| "I'll code first and test later" | Use `test-driven-development` first. |
| "The test failure is obvious" | Use `systematic-debugging` first. |
| "I manually verified it" | Use `verification-before-completion` before success claims. |
| "Task/general-purpose is a Claude thing" | In DSH, use the native `subagent` tool with the completed template. |

## Catalog Health

- DSH watches skill roots (`<project>/.dsh/skills`, `~/.dsh/skills`, etc.) — new or edited skills reach the catalog without a restart.
- If a skill is missing from the catalog: verify the directory is under a scanned root (project root = nearest ancestor containing `.git`, else cwd), the file is `<name>/SKILL.md`, and the frontmatter has a kebab-case `name` plus `description`.
- Run `.dsh/scripts/validate-install.ps1` to check the whole installation.
