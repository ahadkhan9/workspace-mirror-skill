---
name: workspace-mirror
description: Sync and backup Antigravity IDE sessions, transcripts, SQLite database metadata, and brain artifacts locally into a structured workspace directory (.gemini-local/) with a rich interactive viewer.html page. Use this skill whenever the user mentions backups, workspace syncing, fswatch real-time mirroring, viewing transcripts offline, inspecting database schemas, or when they want to set up an offline viewer for their sessions.
---

# Workspace Mirror Skill

This skill allows Antigravity agents to automatically discover, mirror, and back up conversation history and IDE artifacts for the current workspace into a human-readable local folder (`.gemini-local/`).

It also compiles an interactive local HTML viewer (`.gemini-local/viewer.html`) with all synced data pre-embedded (so no folder picker or local server is needed to view it).

---

## When to Trigger
- When the user asks to sync, backup, mirror, or save their current workspace history/session data.
- When the user asks for a local viewer, HTML interface, offline transcript browser, or to inspect SQLite database tables in a rich dashboard.
- When the user wants to set up file-watching with `fswatch` to auto-commit sync changes.

---

## Usage Instructions

The skill bundles a self-contained, dependency-free Python script `mirror.py` inside its `scripts/` directory.

### Running the Mirror

Run the mirror script on behalf of the user using the `run_command` tool. By default, the script detects the current directory as the active workspace, but you can explicitly pass a workspace path.

1. **One-shot sync (Standard)**:
   - *macOS / Linux*:
     ```bash
     python3 ~/.gemini/config/skills/workspace-mirror/scripts/mirror.py
     ```
   - *Windows (PowerShell)*:
     ```powershell
     python $env:USERPROFILE\.gemini\config\skills\workspace-mirror\scripts\mirror.py
     ```

2. **Watch mode (Real-time auto-sync)**:
   Automatically watches the conversation database folder and triggers updates whenever a step finishes. (Requires `fswatch` to be installed on the system):
   - *macOS / Linux*:
     ```bash
     python3 ~/.gemini/config/skills/workspace-mirror/scripts/mirror.py --watch
     ```

3. **Force re-sync**:
   Forces all conversations associated with this workspace to be re-processed:
   - *macOS / Linux*:
     ```bash
     python3 ~/.gemini/config/skills/workspace-mirror/scripts/mirror.py --force
     ```
   - *Windows (PowerShell)*:
     ```powershell
     python $env:USERPROFILE\.gemini\config\skills\workspace-mirror\scripts\mirror.py --force
     ```

4. **Target specific workspace**:
   - *macOS / Linux*:
     ```bash
     python3 ~/.gemini/config/skills/workspace-mirror/scripts/mirror.py --workspace "/path/to/workspace"
     ```
   - *Windows (PowerShell)*:
     ```powershell
     python $env:USERPROFILE\.gemini\config\skills\workspace-mirror\scripts\mirror.py --workspace "C:\path\to\workspace"
     ```

---

## Output Structure

Running the mirror creates a `.gemini-local/` directory inside the target workspace containing:
- **`viewer.html`**: A standalone, zero-click interactive interface containing all transcripts, database schema layouts, and inline media previewers.
- **`index.md`**: A master index markdown linking all session logs.
- **`sessions/`**: Folders organized by session ID containing:
  - `transcript.md`: Transcripts rendered as markdown chat bubbles.
  - `conversation.md`: Metadata logs and SQLite table row counts.
  - `artifacts/`: Local copies of all created artifacts and images.
  - `raw/`: Backup of the raw `.db` binary files.

---

## Recommended Practices

1. **Add to Git**: Recommend that the user commits the `.gemini-local/` folder to track history. Make sure their `.gitignore` contains the following lines to exclude raw binaries and local sync state caches:
   ```gitignore
   .gemini-local/**/raw/
   .gemini-local/.sync_state.json
   ```
2. **Launch Viewer**: After running a sync, always open the generated viewer to present the result to the user:
   ```bash
   open .gemini-local/viewer.html
   ```
