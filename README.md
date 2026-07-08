# 🪞 Antigravity Workspace Mirror Skill

[![GitHub Stars](https://img.shields.io/github/stars/ahadkhan9/workspace-mirror-skill?style=flat-color)](https://github.com/ahadkhan9/workspace-mirror-skill/stargazers)
[![License](https://img.shields.io/github/license/ahadkhan9/workspace-mirror-skill?style=flat-color)](LICENSE)
[![OCI Registry](https://img.shields.io/badge/OCI-ghcr.io-blue?style=flat-color)](https://github.com/ahadkhan9/workspace-mirror-skill/pkgs/container/workspace-mirror-skill)

> [!IMPORTANT]
> This skill is specifically designed and optimized to be **highly useful for the Google Antigravity IDE**, providing developers and agentic workflows with automatic session preservation, offline viewing, and backup capabilities.

An autonomous agent capability (Skill) for the Google Antigravity IDE and other agentic frameworks that automatically backs up, mirrors, and compiles conversation history, IDE database states, and workspace artifacts into a rich, interactive, standalone local dashboard.


---

## ✨ Features

* **📦 Zero-Dependency Syncing**: Reads your agent’s conversation databases and mirrors them locally.
* **🌐 Standalone Viewer**: Compiles a single, dependency-free interactive HTML viewer (`.gemini-local/viewer.html`) with all synced data pre-embedded (no local server or file-picker needed).
* **📝 Markdown Transcripts**: Exports raw chat databases into readable markdown files styled with speech bubbles.
* **📊 DB Schema Inspector**: Inspects internal SQLite tables, counts, and metadata.
* **👁️ Media Previews**: Supports inline images and system-generated artifacts directly in the viewer.
* **🔄 Live Watch Mode**: Watch mode triggers auto-syncing in real-time as steps finish (requires `fswatch`).

---

## 🚀 Installation & Usage

This skill can be installed via OCI registry or Git.

### Method 1: Using `skr` (OCI Registry)

If you have the `skr` (Skill Registry) CLI installed, pull the compiled skill:

```bash
skr install oci://ghcr.io/ahadkhan9/workspace-mirror-skill:v1.0.0
```

### Method 2: Git-Based Installation

Clone this repository directly into your local customizations directory:

* **Global Installation**:
  ```bash
  git clone https://github.com/ahadkhan9/workspace-mirror-skill.git ~/.gemini/config/skills/workspace-mirror
  ```
* **Workspace-Specific Installation**:
  ```bash
  git clone https://github.com/ahadkhan9/workspace-mirror-skill.git .agents/skills/workspace-mirror
  ```

---

## 🛠️ Commands

When the skill is loaded by Antigravity, the agent will execute the underlying `mirror.py` script on your behalf:

* **One-shot sync**:
  ```bash
  python3 scripts/mirror.py
  ```
* **Real-time auto-sync (Watch mode)**:
  ```bash
  python3 scripts/mirror.py --watch
  ```
* **Force re-sync**:
  ```bash
  python3 scripts/mirror.py --force
  ```
* **Target a specific path**:
  ```bash
  python3 scripts/mirror.py --workspace "/path/to/workspace"
  ```

---

## 📂 Output Structure

After running a sync, a `.gemini-local/` folder is generated in your workspace root:

```
.gemini-local/
├── viewer.html         # One-click dashboard to browse all logs
├── index.md           # Master index file of all sessions
└── sessions/
    └── <session-id>/
        ├── transcript.md     # Renders as markdown chat bubbles
        ├── conversation.md   # Metadata and SQLite table row counts
        ├── artifacts/        # Extracted files and images
        └── raw/              # Backup of raw database binary
```

> [!TIP]
> We recommend adding the following lines to your project's `.gitignore` file to avoid checking in large database binaries and caching files:
> ```gitignore
> .gemini-local/**/raw/
> .gemini-local/.sync_state.json
> ```

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
