# AI LifeOS

A local-first personal AI operating system. LifeOS finds and organizes your files, manages tasks and
reminders, remembers what you tell it, and suggests useful actions. It acts only through a fixed set of
permission-checked tools, and it asks before it changes anything.

This repository contains the **MVP** (spec section 10): a desktop app (Tauri + React) backed by a
local Python service (FastAPI + SQLite) with a local model through Ollama.

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
| `llm.py` | Ollama client: status, structured chat, embeddings |
| `scheduler.py` | Background jobs and notifications |
| `main.py` | HTTP API |

## Security model

Each item maps to the spec's section 8.

- **Explicit permission.** The assistant sees nothing until you approve a folder. Your whole home folder, `/` and system folders can't be approved. Paths are resolved, so `..` and symlinks can't be used to escape.
- **Least privilege.** The model can only name tools from the registry. Unknown tools and invalid arguments are rejected and logged. There is no shell tool, and model output is never executed as a command.
- **Confirmation.** File moves, folder creation and memory deletion need approval by default. Destructive tools can never be set to auto. Pausing automation makes every write wait for approval.
- **Untrusted content.** Document text is passed to the model as data inside delimiters, with instructions to ignore any commands it contains. Whatever the model proposes still goes through the same validation and policy checks.
- **Local API hardening.** The API binds to `127.0.0.1`. Every request needs an `X-LifeOS-Client` header, which browsers can't send cross-origin without a CORS preflight that only the app's origins pass. This stops other websites from driving the API.
- **Reversible and logged.** Moves never overwrite: a name collision gets a ` (1)` suffix. Executed actions keep what they did so they can be undone. Every event goes to the activity log.
- **Your data stays local.** SQLite lives in your OS app-data folder (`~/.local/share/ai-lifeos`, `%APPDATA%\AI-LifeOS`, or `LIFEOS_DATA_DIR`). Cloud AI isn't used.

## Running it

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
cd backend && pytest             # 27 tests: MVP flow, security boundaries, features, model planner (with Ollama faked)
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
| 1 Foundation | Done: desktop shell, backend, local model, chat, settings, database |
| 2 Real-world actions | Done: file search, document reading, folder creation, organization with confirmation, tasks and reminders, logs |
| 3 Memory and context | Mostly done: approved memories, semantic + keyword search, memory controls, memory recall in prompts, universal search. Automatic project tracking is next |
| 4 Automation engine | Next: workflow definitions (trigger → conditions → tool steps → approvals → summary), scheduler, editor |
| 5 Proactive intelligence | Started: deadline, clutter, duplicate, large-file and project suggestions, daily summary, configurable level |
| 6 Integrations | Planned: calendar/email/cloud storage connectors, optional cloud models with per-request consent, credentials in the OS keychain |
| 7 Advanced | Planned |

Packaging note: for a release that doesn't depend on a system Python, freeze the backend with PyInstaller as
`lifeos-backend` and place it next to the app executable (Tauri `externalBin`). The shell uses it automatically.

## License

MIT
