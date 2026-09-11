# DSH Tool Mapping

Use this reference when upstream Superpowers instructions mention Claude Code, Trae, Codex, Gemini, or generic agent tools.

| Upstream reference | DSH equivalent |
|---|---|
| `Skill` tool | `skill({ name: "<skill-name>" })` — exact name from the session catalog |
| `superpowers:<skill-name>` | `skill({ name: "<skill-name>" })` |
| `Task tool (general-purpose)` | `subagent` tool with the completed self-contained prompt (background by default; `run_in_background: false` only when the next action depends on the result) |
| Named subagents (`.claude/agents`, `.trae/agents`) | `.dsh/agents/*.md` prompt templates: read, complete, pass as the `subagent` prompt |
| Follow-up that needs this conversation's context | `subagent_fork` (inherits completed turns) |
| Multi-agent fan-out / phased orchestration | `workflow` tool (scripted) |
| Messaging a running subagent | `send_message` (by subagent id); `interrupt_agent` to stop its current turn |
| `TodoWrite` | `todo_write` — always sends the ENTIRE list; it replaces the previous one |
| `Read`, `Write`, `Edit` | `read` / `write` / `edit`; plus `glob` (file patterns), `grep` (content search), `read_image` |
| `Bash` | `pwsh` (PowerShell). Long-running commands: `run_in_background: true`, collect with `job_output`, stop with `job_kill` |
| `CLAUDE.md` | `AGENTS.md` (auto-injected workspace instructions) |
| Hooks (`SessionStart`, `UserPromptSubmit`) | Not needed: AGENTS.md is injected every session and the skill catalog is always visible |
| Local conversation memory scripts | Not used in this package |

When a skill references a prompt template, read that template, complete it, and pass the full text to `subagent`. Use the matching template from `.dsh/agents/` as the role definition when available, but still include the full task prompt and file paths. DSH subagents do not see the parent conversation — never ask them to infer context from chat history.

When a skill references local memory scripts from older Superpowers versions, skip those scripts.
