# AI LifeOS

A local-first personal AI operating system. LifeOS finds and organizes your files, manages tasks and
reminders, remembers what you tell it, and suggests useful actions. It acts only through a fixed set of
permission-checked tools, and it asks before it changes anything.

It is a desktop app (Tauri + React) backed by a local Python service (FastAPI + SQLite). On top of the
MVP it runs a **crew of AI agents**: an Overseer that splits your goal into a task graph and specialists
(planning, files, research, coding, memory, email) that work on it in parallel, each with its own model,
watched and steered from a live **Control Center**. Models run locally through Ollama by default; cloud
models (OpenAI, Anthropic, OpenRouter, Groq, Mistral, DeepSeek, xAI, Gemini or any OpenAI-compatible URL)
are optional, per agent, and behind a daily budget.

## The crew and the Control Center

| Agent | Role | Tools |
| --- | --- | --- |
| **Atlas**, Overseer | Breaks a goal into tasks, picks the specialist for each, sets dependencies, checks results, writes the final report | delegation, `ask_user` |
| **Chrono**, Planner | Schedules, deadlines, day/week plans, calendar | tasks, reminders, calendar events, day plan |
| **Archivist**, Files | Finds, reads and organizes files in approved folders | search, read, organize, create folder, large/old/duplicate finders |
| **Scout**, Research | Web search and page reading (off until you allow web access) | `web_search`, `fetch_url`, outbox |
| **Forge**, Coder | Reads and writes code and project files | project context, read, `write_file` (with approval and undo) |
| **Mnemo**, Memory | Recalls and saves what you told LifeOS | memory search/save |
| **Herald**, Comms | Reads your inbox (read-only IMAP) and drafts messages | `check_email`, outbox |

How a mission runs:

1. You type or dictate a goal in the Control Center (or the Assistant hands it over, or a workflow starts it).
2. Atlas plans a DAG of tasks (model-planned with a JSON schema; rule-based fallback if no model). If the goal is ambiguous it asks you a question first (a **brief**).
3. Tasks run in parallel up to *agents working at once*; each agent runs a step loop (think → tool / ask you / finish) with its own model, step limit and tool list.
4. Every tool call goes through the same permission policy as the assistant: writes queue an approval card in **Needs you**, and the agent waits (without holding a parallel slot).
5. Atlas writes a report to the mission, saves it to the **Outbox** (`%APPDATA%\AI-LifeOS\outbox`), and you get a notification.

The Control Center shows the station (agents as nodes; links light up as work moves between them), each
agent's live state (idle, working, waiting on you, asking), a live event feed, the plan graph and timeline
of each mission, briefs and approvals, the outbox, a token/cost ledger, and per-agent editing (model,
persona, tools, enable). **Stop all** cancels every mission and rejects their pending actions. Crews can
be exported and imported as JSON packs (agents + workflows; imported workflows start disabled).

## How the crew learns

Model weights don't change on a normal PC; the crew gets better the way a team does:

- **Peer review.** Atlas scores every specialist result 1–5 with feedback. A score of 2 or less sends the work back for one revision with that feedback.
- **Shared playbook.** Reviews, Atlas's end-of-mission reflection, failed tasks, approvals you rejected, and notes you leave when you thumbs-down a result all become short *lessons*. Agents read the most relevant lessons (theirs plus whole-crew ones) before each task, so one agent's mistake teaches the rest. Edit, pin or delete them in **Control Center → Learning**.
- **Scorecard and auto-routing.** Every task records its model and score. Give an agent "other models to try" and turn on auto-routing: each model gets 3 tries, then the best-scoring one does the work (with a little ongoing exploration).
- **Training export.** Download tasks scored 4+ (or thumbed up) as chat-format JSONL. To actually fine-tune, train a LoRA on it offline (for example with [Unsloth](https://github.com/unslothai/unsloth) on a GPU), add `ADAPTER ./adapter` to an Ollama Modelfile, `ollama create`, and set that model on the agent.

## The Station

**Station** (sidebar) is a pixel-art world of the crew. Each agent has a room; it walks to its desk when it is working
(screens light up), wanders the lounge when idle, and comes to **your desk** with a `!` (approval) or `?` (question)
when it needs you. Live events show as speech bubbles and delegations fly from the Command Deck. Walk with WASD/arrows
or click; press **E** or click an agent to chat with it in character, give it a task, answer its question or approve
its proposal on the spot. Use your desk to launch a crew mission.

## Automations

Workflows are *trigger → conditions → steps → summary*:

- Triggers: interval, daily, weekly, monthly, on startup, manual.
- Conditions on live metrics: open/overdue tasks, tasks due within N hours, loose files, duplicate MB, large files, pending actions, weekday, hour.
- Steps: any tool (args validated against its schema), a crew mission, or a notification. Steps can reference earlier results (`{{steps.scan.summary}}`), retry, and continue or stop on error.
- Write the workflow in plain English ("Every Friday at 5pm, organize my downloads, summarize my completed tasks and plan next week"), review the draft, save. Six templates included.
- Pause automation (sidebar) stops scheduled runs and makes every write wait for approval.

## Projects, calendar, email, web

- **Projects** are detected in approved folders (git, Node, Python, Rust, Go, .NET, Java, docs) with recent files, open tasks, README and memories; LifeOS suggests resuming stale ones and the crew gets their context.
- **Calendar**: subscribe to ICS URLs or files (Google/Outlook/iCloud "secret address"), recurring events and time zones, overlap warnings, local events, export as `.ics`.
- **Email**: read-only IMAP (app password stored in the OS keychain); Herald can summarize and triage.
- **Web**: `web_search` and `fetch_url` for Scout, off by default; private/local addresses are always blocked.
- **Voice**: dictation in the composer (Web Speech API, where the webview supports it).

## What works

| MVP requirement | Implementation |
| --- | --- |
| Desktop app with a dashboard | Tauri 2 shell, React/TypeScript/Tailwind UI with Dashboard, Assistant, Memory, Files, Calendar, Activity, Settings, and global search |
| Local AI via Ollama | JSON-schema-constrained planning (`/api/chat` with `format`), embeddings (`/api/embed`) for semantic memory, model-based document categorization |
| Natural-language assistant | Hybrid planner: built-in rules handle common commands instantly and offline; everything else goes to the local model; falls back to rules if the model is down |
| Local file search in approved folders | Incremental index of name, type, size, dates and extracted text (PDF, DOCX, text, code) in SQLite FTS5; large, old and duplicate file finders |
| Organization suggestions with confirmation | Category, type or date plans shown as a preview; you can deselect files, then approve or reject; moves never overwrite and can be undone |
| Tasks and reminders | Natural-language dates ("tomorrow 9am", "next friday"), priorities, estimates, a background scheduler that fires reminders, a day planner, and a calendar |
| Persistent conversations and preferences | Conversations, messages, settings and memories in SQLite; memories can be searched, edited, pinned and deleted |
| Activity history and permission settings | Every proposal, decision, action, undo and failure is logged; per-tool policy (auto / confirm / deny); one-click kill switch |

The spec's example request runs end to end:

> Find all PDFs in my Downloads folder, organize them by category, and remind me to review the important ones tomorrow.

1. `search_files` lists the PDFs in the approved Downloads folder.
2. Their text is extracted and each file is categorized (Finance, Programming, Study, Travel, ...).
3. `organize_files` shows a move plan and waits for approval. Nothing moves yet.
4. `create_task` sets a reminder for tomorrow at 09:00 that lists the files flagged as important (invoices, deadlines, exams, ...).
5. After you approve, the files move, the action is logged, and **Undo** puts them back.

This flow is covered by `backend/tests/test_mvp_workflow.py`, and it was also run in a real browser against the built UI.

## Architecture

```
React UI (Tauri webview)  ──HTTP + X-LifeOS-Client header──▶  FastAPI on 127.0.0.1:8765
                                                                 │
   request ─▶ planner (rules ▸ Ollama) ─▶ tool registry ─▶ policy check ─┬─ auto: run + log
                                                                         ├─ confirm: queue action → preview → approve/reject → run + log → undo
                                                                         └─ deny: refuse + log
                                                                 │
                         SQLite (WAL): files + FTS5 · tasks · memories + embeddings · conversations · actions · activity · settings
                         Scheduler thread: reminders (20 s) · re-index (30 min) · embed pending memories
```

`backend/lifeos/`

| Module | Role |
| --- | --- |
| `agent.py` | Request → plan → execute → reply. Builds the model prompt with approved folders, relevant memories and the tool catalog |
| `rules.py` | Deterministic planner for common commands (find, organize, remind, add task, plan, summarize, remember, recall) |
| `tools.py` | The only things the assistant can do: pydantic-validated tools with a risk level, a default policy, preview, execute and undo |
| `actions.py` | Pending actions: atomic approve/reject (no double execution), execution, undo, superseding stale plans |
| `permissions.py` | Approved folders; every path is resolved (symlinks included) and must stay inside one |
| `files.py` | Indexing, text extraction, search, large/old/duplicate detection, organization plans, safe moves and undo |
| `tasks.py`, `timeparse.py` | Tasks, natural-language dates, prioritization, day planning, reminders |
| `memory.py`, `search.py` | Long-term memory (FTS5 + embedding cosine hybrid) and universal search |
| `proactive.py` | Suggestions (deadlines, clutter, duplicates, large unused files, unfinished projects), daily summary, dashboard |
| `llm.py` | Model router: Ollama plus cloud providers (`provider:model` refs), structured chat, embeddings, token/cost ledger, budget checks |
| `vault.py` | API keys and passwords in the OS keychain (`keyring`), DPAPI-protected file fallback on Windows |
| `crew.py` | Agent definitions, defaults, packs |
| `missions.py` | Mission runtime: planning, task DAG, parallel limiter, agent step loop, briefs, approvals, cancel, report |
| `workflows.py` | Automation engine: triggers, conditions, steps, runs, natural-language drafting, templates |
| `projects.py` | Project detection and context |
| `integrations.py`, `web.py` | ICS calendars, IMAP email, web search/fetch with SSRF guard |
| `scheduler.py` | Background jobs: reminders, workflows, project/calendar refresh, notifications |
| `main.py` | HTTP API |

## Security model

Each item maps to the spec's section 8.

- **Explicit permission.** The assistant sees nothing until you approve a folder. Your whole home folder, `/` and system folders can't be approved. Paths are resolved, so `..` and symlinks can't be used to escape.
- **Least privilege.** The model can only name tools from the registry. Unknown tools and invalid arguments are rejected and logged. There is no shell tool, and model output is never executed as a command.
- **Confirmation.** File moves, folder creation and memory deletion need approval by default. Destructive tools can never be set to auto. Pausing automation makes every write wait for approval.
- **Untrusted content.** Document text is passed to the model as data inside delimiters, with instructions to ignore any commands it contains. Whatever the model proposes still goes through the same validation and policy checks.
- **Local API hardening.** The API binds to `127.0.0.1`. Every request needs an `X-LifeOS-Client` header, which browsers can't send cross-origin without a CORS preflight that only the app's origins pass. This stops other websites from driving the API.
- **Reversible and logged.** Moves never overwrite: a name collision gets a ` (1)` suffix. Executed actions keep what they did so they can be undone. Every event goes to the activity log.
- **Your data stays local.** SQLite lives in your OS app-data folder (`~/.local/share/ai-lifeos`, `%APPDATA%\AI-LifeOS`, or `LIFEOS_DATA_DIR`).
- **Cloud is opt-in.** Cloud models are refused unless *Allow cloud models* is on, the provider has a key, and today's spend is under the daily budget. Keys live in the OS keychain, never in the database or the API responses.
- **Agents are contained.** Agents only get the tools listed on them, results from files/web/email are passed back as untrusted data, missions have step and token budgets, and every write still needs your approval by default.

## Running it on Windows (PowerShell)

Assumes the repo at `A:\ai-lifeos`, Python 3.11+, Node 18+, and Ollama with `llama3.1:8b` and `nomic-embed-text`.

```powershell
cd A:\ai-lifeos
git fetch origin
git checkout claude/project-thread-pcka0b
git pull
cd A:\ai-lifeos\backend
python -m pip install -e ".[dev]"
cd A:\ai-lifeos\frontend
npm.cmd install
npm.cmd run build
cd A:\ai-lifeos\backend
python -m lifeos
```

Then open http://127.0.0.1:8765/#/crew. Desktop installer (needs Rust): `powershell -ExecutionPolicy Bypass -File A:\ai-lifeos\scripts\build-windows.ps1`.
It freezes the backend into `lifeos-backend.exe` with PyInstaller, bundles it as a Tauri sidecar, and prints the `.msi`/`.exe` paths.

## Running it (any OS)

Requirements: Python 3.10+, Node 18+, and optionally [Ollama](https://ollama.com) and Rust (for the desktop build).

```bash
# 1. Backend
cd backend
pip install -e ".[dev]"          # or: pip install -r requirements.txt
python -m lifeos                 # http://127.0.0.1:8765

# 2. Local model (optional, enables free-form requests and semantic memory)
ollama pull llama3.1:8b
ollama pull nomic-embed-text

# 3a. Web UI in development
cd frontend
npm install
npm run dev                      # http://localhost:5173

# 3b. Or build once and let the backend serve it at http://127.0.0.1:8765
npm run build

# 3c. Desktop app (starts the backend for you if it isn't running)
npm run tauri dev
npm run tauri build              # installers in src-tauri/target/release/bundle
```

On Linux the desktop build needs `libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev`; see the [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/).

The first time you open the app, go to **Settings → Approved folders** and add a folder such as `~/Downloads`. To use a different model, change it in **Settings → Local AI**. Any Ollama model with good JSON output works, for example `qwen2.5:7b` or `llama3.1:8b`.

Environment variables: `LIFEOS_DATA_DIR`, `LIFEOS_DB`, `LIFEOS_HOST`, `LIFEOS_PORT`, and `LIFEOS_PYTHON` / `LIFEOS_BACKEND_DIR` (used by the desktop shell to launch the backend).

### Tests

```bash
cd backend && pytest             # 73 tests: MVP flow, security, crew/missions, learning, automations, projects, calendar, providers (models faked)
cd frontend && npm run typecheck && npm run build
```

CI runs both on every push (`.github/workflows/ci.yml`).

## Things you can say

- Find all PDFs in my Downloads folder, organize them by category, and remind me to review the important ones tomorrow
- Find the PDF about JavaScript testing that I downloaded a few months ago
- Find the document I worked on last Tuesday
- Find large files I haven't used recently / Find duplicate files
- Organize my Downloads folder by type
- Remind me to finish my programming project tomorrow at 6pm
- Add task write essay due friday 5pm
- Create a schedule for completing my assignments / Plan my day
- Summarize everything important from today
- Remember that my thesis project lives in ~/code/thesis
- What was the solution I found for my pytest error?
- Anything else, such as "Prepare my workspace for studying", goes to the local model

## Roadmap status

| Phase | Status |
| --- | --- |
| 1 Foundation | Done |
| 2 Real-world actions | Done |
| 3 Memory and context | Done: memories, semantic + keyword search, universal search, project tracking and context |
| 4 Automation engine | Done: workflows with triggers, conditions, steps, approvals, summaries, scheduler, editor, natural-language drafting, templates |
| 5 Proactive intelligence | Done: deadline, clutter, duplicate, large-file, project-resume, calendar-overlap and workflow suggestions; daily summary; notification preferences and quiet hours |
| 6 Integrations | Done: ICS calendars, read-only IMAP email, web search/fetch, cloud models per agent with budget, OS keychain |
| 7 Advanced | Started: multi-agent crew with Control Center and pixel Station, learning loop (reviews, lessons, routing, training export), voice dictation, crew packs. Next: cloud storage connectors, signed auto-updates |

Packaging: `scripts/build-windows.ps1` (above). On other OSes run the same PyInstaller command and copy the
binary to `frontend/src-tauri/binaries/lifeos-backend-<target-triple>`, then
`npm run tauri build -- --config src-tauri/tauri.release.conf.json`.

## Credits

The Control Center, the pixel Station and the Overseer/specialist/brief/outbox model were inspired by
[StarNet](https://github.com/androoAGI/starnet) (MIT). No StarNet code is included.

## License

MIT
