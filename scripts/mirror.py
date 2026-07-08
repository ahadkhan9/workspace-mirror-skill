#!/usr/bin/env python3
"""
Gemini Workspace Mirror (sync_gemini.py)

Mirrors Antigravity IDE conversation artifacts from ~/.gemini/antigravity-ide/
into a local .gemini-local/ directory with structured, readable markdown files.
Also generates a self-contained viewer.html with all data embedded — open it
directly in the browser, no folder picker or server needed.

Usage:
    python sync_gemini.py                    # One-shot sync
    python sync_gemini.py --watch            # Watch mode with fswatch
    python sync_gemini.py --force            # Force re-sync all
    python sync_gemini.py --workspace /path  # Explicit workspace path

Features:
    - Discovers conversations tied to the current workspace
    - Converts JSONL transcripts to readable markdown
    - Extracts SQLite DB metadata into summaries
    - Copies brain artifacts (plans, walkthroughs, images)
    - Backs up raw .db files
    - Smart change detection — only re-processes modified files
    - Generates a master index.md with all sessions
    - Generates viewer.html with all data pre-embedded (no folder picker)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_IDE_DIR = Path.home() / ".gemini" / "antigravity-ide"
CONVERSATIONS_DIR = GEMINI_IDE_DIR / "conversations"
BRAIN_DIR = GEMINI_IDE_DIR / "brain"
OUTPUT_DIR_NAME = ".gemini-local"
STATE_FILENAME = ".sync_state.json"

# Regex patterns for cleaning user messages
_META_TAGS = re.compile(
    r"<(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|EPHEMERAL_MESSAGE|"
    r"user_information|mcp_servers|web_application_development|"
    r"ephemeral_message|customizations|user_rules|skills|plugins|"
    r"messaging|knowledge_items|conversation_transcript|artifacts|"
    r"slash_commands|planning_mode|planning_mode_artifacts|guidelines|"
    r"communication_style|identity)>"
    r".*?"
    r"</(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|EPHEMERAL_MESSAGE|"
    r"user_information|mcp_servers|web_application_development|"
    r"ephemeral_message|customizations|user_rules|skills|plugins|"
    r"messaging|knowledge_items|conversation_transcript|artifacts|"
    r"slash_commands|planning_mode|planning_mode_artifacts|guidelines|"
    r"communication_style|identity)>",
    re.DOTALL,
)

_USER_REQUEST = re.compile(
    r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL
)

# Icons and labels for step types
_STEP_ICONS: dict[str, tuple[str, str]] = {
    "USER_INPUT": ("👤", "User Message"),
    "PLANNER_RESPONSE": ("🤖", "Agent Response"),
    "CONVERSATION_HISTORY": ("📜", "Context Loaded"),
    "KNOWLEDGE_ITEMS": ("📚", "Knowledge Items"),
    "SYSTEM": ("⚙️", "System"),
}


# ---------------------------------------------------------------------------
# Viewer HTML Template — __GEMINI_DATA_JSON__ is replaced at generate time
# ---------------------------------------------------------------------------

_VIEWER_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Gemini Session Viewer</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<style>
:root{
  --bg:#0d0f14;--surface:#13161d;--surface2:#1a1e28;--surface3:#212633;
  --border:#272d3d;--border2:#2e3548;
  --text:#e2e6f0;--text-muted:#7a8499;--text-dim:#4a5268;
  --accent:#7c6ff7;--accent-s:#5a51d4;--accent-glow:rgba(124,111,247,.15);
  --green:#4ade80;--amber:#fbbf24;--rose:#fb7185;--sky:#38bdf8;
  --sidebar-w:280px;--radius:10px;--radius-lg:16px;
  --mono:'JetBrains Mono',monospace;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth;color-scheme:dark}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100dvh;overflow-x:hidden;line-height:1.6}

/* Layout */
.app{display:grid;grid-template-columns:var(--sidebar-w) 1fr;grid-template-rows:48px 1fr;grid-template-areas:"topbar topbar" "sidebar main";height:100dvh}

/* Topbar */
.topbar{grid-area:topbar;display:flex;align-items:center;gap:12px;padding:0 16px;background:var(--surface);border-bottom:1px solid var(--border);z-index:30}
.topbar-logo{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--text);letter-spacing:.01em}
.topbar-logo svg{color:var(--accent)}
.topbar-divider{width:1px;height:20px;background:var(--border)}
.workspace-badge{font-size:11px;font-weight:500;color:var(--text-muted);background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:2px 8px;font-family:var(--mono)}
.topbar-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.sync-label{font-size:11px;color:var(--text-dim);display:flex;align-items:center;gap:5px}
.sync-dot{width:6px;height:6px;border-radius:50%;background:var(--green);flex-shrink:0}

/* Sidebar */
.sidebar{grid-area:sidebar;background:var(--surface);border-right:1px solid var(--border);overflow-y:auto;display:flex;flex-direction:column}
.sidebar::-webkit-scrollbar{width:4px}
.sidebar::-webkit-scrollbar-thumb{background:var(--border2);border-radius:4px}
.sidebar-hdr{padding:12px 16px;border-bottom:1px solid var(--border);position:sticky;top:0;background:var(--surface);z-index:2}
.sidebar-label{font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--text-dim)}
.session-count{font-size:11px;color:var(--text-muted);margin-top:2px}
.session-list{list-style:none;padding:8px 0;flex:1}
.session-btn{display:block;width:100%;text-align:left;padding:10px 16px;background:none;border:none;cursor:pointer;color:var(--text-muted);transition:background .12s,color .12s;border-left:2px solid transparent;font-family:inherit}
.session-btn:hover{background:var(--surface2);color:var(--text)}
.session-btn.active{background:var(--accent-glow);border-left-color:var(--accent);color:var(--text)}
.session-msg{font-size:12px;font-weight:500;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.4}
.session-meta{display:flex;align-items:center;gap:6px;margin-top:4px;flex-wrap:wrap}
.session-date{font-size:10px;color:var(--text-dim);font-family:var(--mono)}
.pill{font-size:9px;font-weight:500;padding:1px 5px;border-radius:4px}
.pill-steps{background:rgba(56,189,248,.12);color:var(--sky)}
.pill-art{background:rgba(74,222,128,.12);color:var(--green)}

/* Main */
.main{grid-area:main;overflow-y:auto;display:flex;flex-direction:column;scrollbar-gutter:stable}
.main::-webkit-scrollbar{width:6px}
.main::-webkit-scrollbar-thumb{background:var(--border);border-radius:6px}

/* Empty state */
.empty-state{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:60px;text-align:center;color:var(--text-dim)}
.empty-icon{font-size:40px}
.empty-title{font-size:18px;font-weight:600;color:var(--text-muted)}
.empty-sub{font-size:13px;max-width:320px;line-height:1.7}

/* Session header */
.session-hdr{padding:16px 28px;border-bottom:1px solid var(--border);background:var(--surface);position:sticky;top:0;z-index:10}
.session-id{font-size:10px;font-family:var(--mono);color:var(--text-dim);margin-bottom:4px}
.session-title{font-size:15px;font-weight:600;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.session-stats{display:flex;gap:16px;margin-top:8px;flex-wrap:wrap}
.stat{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--text-muted)}
.stat-dot{width:5px;height:5px;border-radius:50%;flex-shrink:0}

/* Tabs */
.tab-strip{display:flex;padding:0 28px;border-bottom:1px solid var(--border);background:var(--surface)}
.tab-btn{padding:10px 16px;font-size:12px;font-weight:500;color:var(--text-dim);background:none;border:none;border-bottom:2px solid transparent;cursor:pointer;transition:all .15s;font-family:inherit}
.tab-btn:hover{color:var(--text-muted)}
.tab-btn.active{color:var(--accent);border-bottom-color:var(--accent)}

/* Content panels */
.panel{display:none;padding:28px;flex:1}
.panel.visible{display:block}

/* Transcript */
.step-group{margin-bottom:6px;max-width:1100px;width:100%}
.step-bubble{border-radius:var(--radius-lg);padding:16px 20px}
.step-user .step-bubble{background:var(--accent-glow);border:1px solid rgba(124,111,247,.25)}
.step-agent .step-bubble{background:var(--surface);border:1px solid var(--border)}
.step-ts{font-size:10px;font-family:var(--mono);color:var(--text-dim);margin-bottom:6px;display:flex;align-items:center;gap:6px}
.step-label{font-size:10px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;padding:2px 6px;border-radius:4px}
.label-user{background:rgba(124,111,247,.2);color:var(--accent)}
.label-agent{background:rgba(56,189,248,.15);color:var(--sky)}
.step-text{font-size:13.5px;line-height:1.7;color:var(--text);word-break:break-word}
.step-text p,.preview-content p{margin-bottom:12px;text-wrap:pretty;max-width:800px}
.step-text p:last-child,.preview-content p:last-child{margin-bottom:0}
.step-text h1,.step-text h2,.step-text h3,.preview-content h1,.preview-content h2,.preview-content h3{margin-top:18px;margin-bottom:10px;color:var(--text);font-weight:600;line-height:1.3;text-wrap:balance}
.step-text h1,.preview-content h1{font-size:18px;border-bottom:1px solid var(--border);padding-bottom:4px}
.step-text h2,.preview-content h2{font-size:15px}
.step-text h3,.preview-content h3{font-size:13.5px}
.step-text blockquote,.preview-content blockquote{margin:12px 0;padding:10px 16px;background:var(--surface2);border-left:3px solid var(--accent);border-radius:0 var(--radius) var(--radius) 0;color:var(--text-muted);font-style:italic;text-wrap:pretty;max-width:800px}
.step-text ul,.step-text ol,.preview-content ul,.preview-content ol{margin-bottom:12px;padding-left:20px;max-width:800px}
.step-text li,.preview-content li{margin-bottom:4px;text-wrap:pretty}
.step-text code,.preview-content code{font-family:var(--mono);font-size:12px;color:var(--sky)}
.step-text p code,.step-text li code,.preview-content p code,.preview-content li code{background:var(--surface2);border:1px solid var(--border);border-radius:4px;padding:1px 4px}
.step-text pre code,.preview-content pre code{display:block;padding:14px 18px;overflow-x:auto;line-height:1.5;color:var(--text);background:none;border:none}
.step-text pre code::-webkit-scrollbar,.preview-content pre code::-webkit-scrollbar{height:4px}
.step-text pre code::-webkit-scrollbar-thumb,.preview-content pre code::-webkit-scrollbar-thumb{background:var(--border2);border-radius:4px}
.code-block-container{margin:12px 0;background:#090b0e;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.code-block-container pre{margin:0!important;border:none!important;border-radius:0!important}
.code-block-header{display:flex;align-items:center;justify-content:space-between;padding:6px 14px;background:var(--surface2);border-bottom:1px solid var(--border);font-family:var(--mono);font-size:10px;font-weight:500;color:var(--text-dim)}
.step-text table,.preview-content table{width:100%;border-collapse:collapse;margin:16px 0;font-size:12.5px}
.step-text th,.preview-content th{text-align:left;padding:8px 12px;background:var(--surface2);border:1px solid var(--border);font-weight:600;color:var(--text-muted)}
.step-text td,.preview-content td{padding:8px 12px;border:1px solid var(--border);color:var(--text-muted)}
.step-text tr:hover td,.preview-content tr:hover td{background:var(--surface2)}
.step-text hr,.preview-content hr{border:0;height:1px;background:var(--border);margin:16px 0}
.step-text a,.preview-content a{color:var(--accent);text-decoration:none;border-bottom:1px dotted var(--accent);transition:all .15s ease}
.step-text a:hover,.preview-content a:hover{color:#fff;border-bottom-color:#fff}

/* Tool disclosure */
.tool-disc{margin-top:10px}
.tool-toggle{display:flex;align-items:center;gap:6px;font-size:11px;font-weight:500;color:var(--text-muted);background:var(--surface2);border:1px solid var(--border);border-radius:7px;padding:5px 10px;cursor:pointer;transition:all .15s;font-family:inherit}
.tool-toggle:hover{background:var(--surface3);color:var(--text)}
.tool-toggle svg{transition:rotate .2s;flex-shrink:0}
.tool-toggle[aria-expanded=true] svg{rotate:90deg}
.tool-body{margin-top:8px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.tool-item{padding:12px 14px;border-bottom:1px solid var(--border)}
.tool-item:last-child{border-bottom:none}
.tool-name{font-size:11px;font-family:var(--mono);font-weight:500;color:var(--amber);margin-bottom:6px}
.tool-args{font-size:11px;font-family:var(--mono);color:var(--text-muted);background:var(--bg);border-radius:6px;padding:8px 10px;overflow:auto;white-space:pre;max-height:180px}
.tool-args::-webkit-scrollbar{width:3px;height:3px}
.tool-args::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px}

/* System step collapse */
.sys-disc{margin-bottom:4px}
.sys-toggle{display:flex;align-items:center;gap:5px;font-size:10px;color:var(--text-dim);background:none;border:none;cursor:pointer;padding:3px 6px;border-radius:5px;transition:all .1s;font-family:inherit}
.sys-toggle:hover{background:var(--surface2);color:var(--text-muted)}
.sys-toggle svg{transition:rotate .2s}
.sys-toggle[aria-expanded=true] svg{rotate:90deg}
.sys-body{margin:4px 0 4px 16px;padding:8px 12px;background:var(--surface);border:1px solid var(--border);border-radius:7px;font-size:11px;color:var(--text-dim);font-family:var(--mono);max-height:120px;overflow-y:auto;white-space:pre-wrap}

/* Metadata */
.meta-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-bottom:20px}
.meta-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px}
.meta-card-label{font-size:10px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:var(--text-dim);margin-bottom:6px}
.meta-card-value{font-size:20px;font-weight:600;color:var(--text);font-family:var(--mono)}
.meta-card-sub{font-size:11px;color:var(--text-muted);margin-top:2px}
.section-label{font-size:11px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:var(--text-dim);margin-bottom:10px}
.meta-table{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:20px}
.meta-table th{text-align:left;padding:8px 12px;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--text-dim);border-bottom:1px solid var(--border)}
.meta-table td{padding:7px 12px;color:var(--text-muted);border-bottom:1px solid var(--border);font-family:var(--mono)}
.meta-table tr:last-child td{border-bottom:none}
.meta-table tr:hover td{background:var(--surface2)}

/* Artifacts */
.art-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
.art-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px;cursor:default;transition:all .15s;display:flex;flex-direction:column;gap:6px}
.art-card.clickable{cursor:pointer}
.art-card.clickable:hover{border-color:var(--border2);background:var(--surface2);transform:translateY(-1px)}
.art-icon{font-size:22px}
.art-name{font-size:12px;font-weight:500;color:var(--text);word-break:break-all}
.art-size{font-size:10px;color:var(--text-dim);font-family:var(--mono)}

/* Artifact preview overlay */
.art-preview{position:fixed;inset:48px 0 0 var(--sidebar-w);background:var(--bg);overflow-y:auto;z-index:20;padding:28px;display:none}
.art-preview.open{display:block}
.art-preview::-webkit-scrollbar{width:6px}
.art-preview::-webkit-scrollbar-thumb{background:var(--border);border-radius:6px}
.preview-bar{display:flex;align-items:center;gap:12px;margin-bottom:20px}
.btn-back{display:flex;align-items:center;gap:6px;padding:6px 12px;border-radius:7px;font-size:12px;font-weight:500;cursor:pointer;border:1px solid var(--border);background:var(--surface2);color:var(--text-muted);transition:all .15s;font-family:inherit}
.btn-back:hover{background:var(--surface3);color:var(--text)}
.preview-name{font-family:var(--mono);font-size:12px;color:var(--text-muted)}
.preview-content{max-width:1000px;font-size:13.5px;line-height:1.7;color:var(--text);word-break:break-word}


/* Loading */
.loading{display:flex;align-items:center;justify-content:center;gap:10px;padding:60px;color:var(--text-muted);font-size:13px}
.spinner{width:16px;height:16px;border:2px solid var(--border2);border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{rotate:360deg}}

@media(max-width:640px){
  .app{grid-template-columns:1fr;grid-template-areas:"topbar" "main"}
  .sidebar{display:none}
  .art-preview{inset:48px 0 0 0}
}
/* Floating Jump Widget */
.jump-widget{position:fixed;bottom:24px;right:24px;z-index:100;display:flex;align-items:center;gap:12px;background:rgba(19,22,29,0.75);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,0.06);box-shadow:0 8px 32px rgba(0,0,0,0.4);border-radius:30px;padding:6px 14px;transition:opacity 0.2s,visibility 0.2s}
.jump-widget.hidden{opacity:0;visibility:hidden;pointer-events:none}
.jump-btn{background:none;border:none;color:var(--text-muted);cursor:pointer;display:flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:50%;transition:all .15s ease}
.jump-btn:hover{color:var(--accent);background:var(--surface2);transform:scale(1.15)}
.jump-info{font-family:var(--mono);font-size:11px;color:var(--text);font-weight:500;min-width:40px;text-align:center;user-select:none}

/* Active Highlight Pulsing */
.step-bubble.pulse-active{animation:bubblePulse 2.5s ease-out}
@keyframes bubblePulse{
  0%{box-shadow:0 0 0 0 rgba(124,111,247,0.65);border-color:rgba(124,111,247,0.85);transform:scale(1.005)}
  30%{box-shadow:0 0 0 12px rgba(124,111,247,0);border-color:rgba(124,111,247,0.85);transform:scale(1.005)}
  100%{box-shadow:0 0 0 0 rgba(124,111,247,0);border-color:var(--border);transform:scale(1)}
}
/* Search Box */
.search-box {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 5px 12px;
  transition: border-color .15s, box-shadow .15s;
}
.search-box:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}
.search-icon {
  font-size: 11px;
  color: var(--text-dim);
}
#js-search-input {
  flex: 1;
  background: none;
  border: none;
  outline: none;
  font-family: inherit;
  font-size: 11.5px;
  color: var(--text);
  min-width: 0;
}
#js-search-input::placeholder {
  color: var(--text-dim);
}
.search-count {
  font-family: var(--mono);
  font-size: 9px;
  color: var(--text-muted);
  background: var(--surface3);
  padding: 1px 5px;
  border-radius: 4px;
  white-space: nowrap;
}
.search-clear {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 0 2px;
  transition: color .15s;
  display: flex;
  align-items: center;
  justify-content: center;
}
.search-clear:hover {
  color: var(--rose);
}
.search-clear.hidden, .search-count.hidden {
  display: none;
}

/* Search Highlights */
mark.search-match {
  background: rgba(251, 191, 36, 0.25);
  color: #fff;
  border-bottom: 2px solid var(--amber);
  border-radius: 2px;
  padding: 0 1px;
}
/* Sidebar Footer & Shortcuts Legend */
.sidebar-footer {
  padding: 16px;
  border-top: 1px solid var(--border);
  background: var(--surface);
  font-size: 11px;
  flex-shrink: 0;
}
.footer-label {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--text-dim);
  margin-bottom: 8px;
}
.shortcut-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
  color: var(--text-muted);
}
.shortcut-row:last-child {
  margin-bottom: 0;
}
.shortcut-row kbd {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 5px;
  font-family: var(--mono);
  font-size: 9.5px;
  color: var(--text);
  box-shadow: 0 1px 0 var(--border);
}
</style>
</head>
<body>
<div class="app">

  <!-- Topbar -->
  <header class="topbar">
    <div class="topbar-logo">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
      </svg>
      Gemini Sessions
    </div>
    <div class="topbar-divider" aria-hidden="true"></div>
    <span class="workspace-badge" id="js-workspace">—</span>
    <div class="topbar-right">
      <div class="sync-label">
        <span class="sync-dot" aria-hidden="true"></span>
        <span id="js-synced">—</span>
      </div>
    </div>
  </header>

  <!-- Sidebar -->
  <nav class="sidebar" aria-label="Sessions">
    <div class="sidebar-hdr">
      <div class="sidebar-label">Sessions</div>
      <div class="session-count" id="js-count">—</div>
      <div class="search-box">
        <span class="search-icon" aria-hidden="true">🔍</span>
        <input type="text" id="js-search-input" placeholder="Search transcripts... [/]" autocomplete="off" aria-label="Search sessions and transcripts" />
        <span class="search-count hidden" id="js-search-count">0/0</span>
        <button class="search-clear hidden" id="js-search-clear" title="Clear search [Esc]" aria-label="Clear search">×</button>
      </div>
    </div>
    <ul class="session-list" id="js-list" role="list"></ul>
    <div class="sidebar-footer">
      <div class="footer-label">Keyboard Shortcuts</div>
      <div class="shortcut-row"><span><kbd>/</kbd></span><span>Focus search</span></div>
      <div class="shortcut-row"><span><kbd>Esc</kbd></span><span>Clear search</span></div>
      <div class="shortcut-row"><span><kbd>J</kbd> / <kbd>K</kbd></span><span>Next/Prev user step</span></div>
      <div class="shortcut-row"><span><kbd>Enter</kbd></span><span>Next search match</span></div>
    </div>
  </nav>

  <!-- Main -->
  <main class="main" id="js-main">
    <div class="empty-state" id="js-welcome">
      <span class="empty-icon" aria-hidden="true">🪞</span>
      <div class="empty-title">Select a session</div>
      <div class="empty-sub">Pick any session from the sidebar to view its transcript, metadata, and artifacts.</div>
    </div>

    <div id="js-session" style="display:none;flex-direction:column;flex:1">
      <div class="session-hdr">
        <div class="session-id" id="js-sv-id">—</div>
        <div class="session-title" id="js-sv-title">—</div>
        <div class="session-stats" id="js-sv-stats"></div>
      </div>
      <div class="tab-strip" role="tablist">
        <button class="tab-btn active" role="tab" data-tab="transcript">Transcript</button>
        <button class="tab-btn" role="tab" data-tab="metadata">Metadata</button>
        <button class="tab-btn" role="tab" data-tab="artifacts">Artifacts</button>
      </div>
      <div class="panel visible" id="panel-transcript"></div>
      <div class="panel" id="panel-metadata"></div>
      <div class="panel" id="panel-artifacts"></div>
    </div>
  </main>

  <!-- Floating Jump Widget -->
  <div class="jump-widget hidden" id="js-jump-widget">
    <button class="jump-btn" id="js-jump-prev" title="Previous user message [K]">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
        <polyline points="18 15 12 9 6 15"/>
      </svg>
    </button>
    <span class="jump-info" id="js-jump-info">0 / 0</span>
    <button class="jump-btn" id="js-jump-next" title="Next user message [J]">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
        <polyline points="6 9 12 15 18 9"/>
      </svg>
    </button>
  </div>

  <!-- Artifact preview overlay -->
  <div class="art-preview" id="js-preview" role="dialog" aria-modal="true" aria-label="Artifact preview">
    <div class="preview-bar">
      <button class="btn-back" id="js-preview-back">← Back</button>
      <span class="preview-name" id="js-preview-name">—</span>
    </div>
    <div class="preview-content" id="js-preview-body"></div>
  </div>

</div>

<script>
/* ── Data ──────────────────────────────────────────────────────── */
const DATA = __GEMINI_DATA_JSON__;
const {meta, sessions} = DATA;
let activeId = null;
let userSteps = [];
let currentUserStepIndex = -1;

/* ── Boot ──────────────────────────────────────────────────────── */
document.getElementById('js-workspace').textContent = meta.workspace;
document.getElementById('js-synced').textContent = meta.syncedAt;
document.getElementById('js-count').textContent = `${meta.totalSessions} session${meta.totalSessions !== 1 ? 's' : ''}`;

renderSidebar();

/* ── Sidebar ───────────────────────────────────────────────────── */
function renderSidebar() {
  const list = document.getElementById('js-list');
  list.innerHTML = '';
  for (const s of sessions) {
    const li = document.createElement('li');
    li.innerHTML = `
      <button class="session-btn" data-id="${esc(s.id)}">
        <div class="session-msg">${esc(s.firstMsg)}</div>
        <div class="session-meta">
          <span class="session-date">${esc(s.date)}</span>
          <span class="pill pill-steps">${esc(s.steps)} steps</span>
          ${s.artifactCount ? `<span class="pill pill-art">${s.artifactCount} files</span>` : ''}
        </div>
      </button>`;
    list.appendChild(li);
  }
}

document.getElementById('js-list').addEventListener('click', e => {
  const btn = e.target.closest('.session-btn');
  if (btn) loadSession(btn.dataset.id);
});

document.getElementById('js-list').addEventListener('keydown', e => {
  const btns = [...document.querySelectorAll('.session-btn')];
  const i = btns.indexOf(document.activeElement);
  if (e.key === 'ArrowDown') { e.preventDefault(); btns[Math.min(i+1, btns.length-1)]?.focus(); }
  if (e.key === 'ArrowUp')   { e.preventDefault(); btns[Math.max(i-1, 0)]?.focus(); }
  if (e.key === 'Enter')     document.activeElement.click();
});

/* ── Session loading ───────────────────────────────────────────── */
function loadSession(id) {
  if (activeId === id) return;
  activeId = id;
  userSteps = [];
  currentUserStepIndex = -1;

  document.querySelectorAll('.session-btn').forEach(b => b.classList.toggle('active', b.dataset.id === id));

  const s = sessions.find(x => x.id === id);
  if (!s) return;

  document.getElementById('js-welcome').style.display = 'none';
  const sv = document.getElementById('js-session');
  sv.style.display = 'flex';

  document.getElementById('js-sv-id').textContent = id;
  document.getElementById('js-sv-title').textContent = s.firstMsg;
  document.getElementById('js-sv-stats').innerHTML = `
    <span class="stat"><span class="stat-dot" style="background:var(--sky)" aria-hidden="true"></span>${esc(s.steps)} steps</span>
    <span class="stat"><span class="stat-dot" style="background:var(--green)" aria-hidden="true"></span>${esc(s.date)}</span>
    <span class="stat"><span class="stat-dot" style="background:var(--amber)" aria-hidden="true"></span>${s.userCount} user · ${s.agentCount} agent</span>`;

  switchTab('transcript');
  renderTranscript(s.transcript, document.getElementById('panel-transcript'));
  document.getElementById('panel-metadata').innerHTML = '';
  document.getElementById('panel-artifacts').innerHTML = '';

  if (document.getElementById('js-search-input').value) {
    performSearch();
  }
}

/* ── Tabs ──────────────────────────────────────────────────────── */
function switchTab(id) {
  document.querySelectorAll('.tab-btn').forEach(b => {
    const active = b.dataset.tab === id;
    b.classList.toggle('active', active);
    b.setAttribute('aria-selected', active);
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('visible', p.id === `panel-${id}`));
  updateJumpWidget();
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    switchTab(tab);
    const s = sessions.find(x => x.id === activeId);
    if (!s) return;
    if (tab === 'metadata' && !document.getElementById('panel-metadata').children.length)
      renderMetadata(s.metadata, document.getElementById('panel-metadata'));
    if (tab === 'artifacts' && !document.getElementById('panel-artifacts').children.length)
      renderArtifacts(s.artifacts, document.getElementById('panel-artifacts'));
  });
});

/* ── Transcript renderer ───────────────────────────────────────── */
function renderTranscript(md, container) {
  container.innerHTML = '';
  const rawSteps = md.split(/(?=^## Step \d+ —)/m).filter(s => s.trim());
  if (!rawSteps.length) { container.innerHTML = emptyHtml('📭', 'No transcript data'); return; }

  const frag = document.createDocumentFragment();
  for (const raw of rawSteps) {
    const lines = raw.split('\n');
    const m = lines[0].match(/^## Step (\d+) — (.+)$/);
    if (!m) continue;

    const [, stepNum, stepLabel] = m;
    const body = lines.slice(1).join('\n').trim();
    const isUser  = stepLabel.includes('👤');
    const isAgent = stepLabel.includes('🤖');

    if (!isUser && !isAgent) {
      frag.appendChild(makeSystemStep(stepNum, stepLabel, body));
      continue;
    }

    const group  = el('div', `step-group step-${isUser ? 'user' : 'agent'}`);
    const bubble = el('div', 'step-bubble');

    // Timestamp
    const tsMatch = body.match(/^\*(.+?)\*/m);
    const tsDiv = el('div', 'step-ts');
    tsDiv.innerHTML = `<span class="step-label ${isUser ? 'label-user' : 'label-agent'}">${isUser ? 'You' : 'Agent'}</span>${esc(tsMatch ? tsMatch[1] : '')}`;
    bubble.appendChild(tsDiv);

    // Separate details blocks from main text
    const detailsBlocks = [];
    let mainText = body
      .replace(/<details>[\s\S]*?<\/details>/g, m => { detailsBlocks.push(m); return ''; })
      .replace(/^\*.+?\*\s*/m, '')
      .replace(/^---\s*$/gm, '')
      .trim();

    const textDiv = el('div', 'step-text');
    textDiv.innerHTML = renderMd(mainText);
    bubble.appendChild(textDiv);

    // Tool calls
    const toolCalls = [];
    for (const d of detailsBlocks) {
      const sm = d.match(/<summary>(.+?)<\/summary>/);
      if (sm && sm[1].includes('🔧')) toolCalls.push(...parseToolCalls(d));
    }
    if (toolCalls.length) bubble.appendChild(makeToolDisc(toolCalls));

    group.appendChild(bubble);
    frag.appendChild(group);
  }
  container.appendChild(frag);
  userSteps = [...container.querySelectorAll('.step-group.step-user')];
  currentUserStepIndex = -1;
  updateJumpWidget();
}

function parseToolCalls(details) {
  const items = [];
  const re = /\*\*`([^`]+)`\*\*\s*```(?:json)?\s*([\s\S]*?)```/g;
  let m;
  while ((m = re.exec(details)) !== null) items.push({name: m[1], args: m[2].trim()});
  return items;
}

function makeToolDisc(calls) {
  const uid = `t${Math.random().toString(36).slice(2)}`;
  const disc = el('div', 'tool-disc');
  const toggle = el('button', 'tool-toggle');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', uid);
  toggle.innerHTML = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>🔧 ${calls.length} tool call${calls.length > 1 ? 's' : ''}`;

  const body = el('div', 'tool-body');
  body.id = uid;
  body.hidden = true;

  for (const c of calls) {
    let display = c.args;
    try {
      const p = JSON.parse(c.args);
      const clean = {};
      for (const [k, v] of Object.entries(p)) {
        let val = v;
        if (typeof val === 'string') { try { val = JSON.parse(val); } catch(_){} }
        clean[k] = val;
      }
      display = JSON.stringify(clean, null, 2);
    } catch(_) {}

    const item = el('div', 'tool-item');
    item.innerHTML = `<div class="tool-name">⚡ ${esc(c.name)}</div><pre class="tool-args">${esc(display)}</pre>`;
    body.appendChild(item);
  }

  toggle.addEventListener('click', () => {
    const exp = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!exp));
    body.hidden = exp;
  });
  disc.appendChild(toggle);
  disc.appendChild(body);
  return disc;
}

function makeSystemStep(num, label, body) {
  const uid = `s${Math.random().toString(36).slice(2)}`;
  const cleanBody = body.replace(/<details>[\s\S]*?<\/details>/g, '').trim();
  const d = el('div', 'sys-disc');
  const toggle = el('button', 'sys-toggle');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', uid);
  toggle.innerHTML = `<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>Step ${esc(num)} — ${esc(label.replace(/[👤🤖📜📚⚙️❓]/gu,'').trim())} · ${cleanBody.length.toLocaleString()} chars`;
  const bodyEl = el('pre', 'sys-body');
  bodyEl.id = uid;
  bodyEl.hidden = true;
  bodyEl.textContent = cleanBody.slice(0, 1500) + (cleanBody.length > 1500 ? '\n…' : '');
  toggle.addEventListener('click', () => {
    const exp = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!exp));
    bodyEl.hidden = exp;
  });
  d.appendChild(toggle);
  d.appendChild(bodyEl);
  return d;
}

/* ── Metadata renderer ─────────────────────────────────────────── */
function renderMetadata(md, container) {
  container.innerHTML = '';
  const sizeM   = md.match(/\*\*Size\*\*:\s*([\d,]+ bytes)/);
  const dbM     = md.match(/\*\*Database\*\*:\s*`([^`]+)`/);
  const stepsM  = md.match(/\*\*Total steps\*\*:\s*(\d+)/);
  const genM    = md.match(/\*\*Total payload\*\*:\s*([\d,]+ bytes) \(([\d.]+ KB)\)/);
  const entM    = md.match(/\*\*Entries\*\*:\s*(\d+)/);

  const grid = el('div', 'meta-grid');
  for (const c of [
    {label:'Database',    value: dbM    ? dbM[1].slice(0,8)+'…'                  : '—', sub: 'SQLite'},
    {label:'Total steps', value: stepsM ? stepsM[1]                              : '—', sub: 'recorded'},
    {label:'DB size',     value: sizeM  ? sizeM[1].replace(' bytes','B')         : '—', sub: 'on disk'},
    {label:'Gen payload', value: genM   ? genM[2]                                : '—', sub: entM ? `${entM[1]} entries` : ''},
  ]) {
    grid.innerHTML += `<div class="meta-card">
      <div class="meta-card-label">${esc(c.label)}</div>
      <div class="meta-card-value">${esc(c.value)}</div>
      ${c.sub ? `<div class="meta-card-sub">${esc(c.sub)}</div>` : ''}
    </div>`;
  }
  container.appendChild(grid);

  // Step type table
  const stMatch = md.match(/## Step Statistics[\s\S]*?\n\| Step Type[\s\S]*?(?=\n##|$)/);
  if (stMatch) {
    const lbl = el('div', 'section-label'); lbl.textContent = 'Step type breakdown'; container.appendChild(lbl);
    const t = el('table', 'meta-table');
    t.innerHTML = '<thead><tr><th>Type (int)</th><th>Count</th></tr></thead><tbody></tbody>';
    for (const row of (stMatch[0].match(/\| \d+ \| \d+ \|/g) || [])) {
      const [type, count] = row.split('|').map(c=>c.trim()).filter(Boolean);
      t.querySelector('tbody').innerHTML += `<tr><td>${esc(type)}</td><td>${esc(count)}</td></tr>`;
    }
    container.appendChild(t);
  }

  // DB tables
  const dbTMatch = md.match(/## Database Tables([\s\S]+?)(?=\n##|$)/);
  if (dbTMatch) {
    const lbl = el('div', 'section-label'); lbl.textContent = 'Database tables'; container.appendChild(lbl);
    const t = el('table', 'meta-table');
    t.innerHTML = '<thead><tr><th>Table</th><th>Rows</th></tr></thead><tbody></tbody>';
    for (const line of dbTMatch[1].split('\n').filter(l => l.startsWith('- `'))) {
      const m = line.match(/- `([^`]+)`:\s*(.*)/);
      if (m) t.querySelector('tbody').innerHTML += `<tr><td><code style="font-family:var(--mono);font-size:11px;color:var(--sky)">${esc(m[1])}</code></td><td>${esc(m[2])}</td></tr>`;
    }
    container.appendChild(t);
  }
}

/* ── Artifacts renderer ────────────────────────────────────────── */
function renderArtifacts(artifacts, container) {
  container.innerHTML = '';
  if (!artifacts.length) { container.innerHTML = emptyHtml('📂','No artifacts for this session'); return; }
  const grid = el('div', 'art-grid');
  for (const a of artifacts) {
    const isImage = /\.(png|jpg|jpeg|gif|webp|svg)$/i.test(a.name);
    const isAudio = /\.(mp3|wav|ogg|m4a)$/i.test(a.name);
    const isVideo = /\.(mp4|webm|ogv|mov)$/i.test(a.name);
    const isMedia = isImage || isAudio || isVideo;
    const isClickable = a.content != null || isMedia;

    const card = el('div', `art-card${isClickable ? ' clickable' : ''}`);
    card.innerHTML = `<div class="art-icon" aria-hidden="true">${artIcon(a.name)}</div><div class="art-name">${esc(a.name)}</div><div class="art-size">${fmtBytes(a.size)}</div>`;
    if (isClickable) {
      card.setAttribute('role','button');
      card.setAttribute('tabindex','0');
      card.addEventListener('click', () => openPreview(a));
      card.addEventListener('keydown', e => { if(e.key==='Enter') openPreview(a); });
    }
    grid.appendChild(card);
  }
  container.appendChild(grid);
}

function openPreview(a) {
  document.getElementById('js-preview-name').textContent = a.name;
  const isImage = /\.(png|jpg|jpeg|gif|webp|svg)$/i.test(a.name);
  const isAudio = /\.(mp3|wav|ogg|m4a)$/i.test(a.name);
  const isVideo = /\.(mp4|webm|ogv|mov)$/i.test(a.name);
  const body = document.getElementById('js-preview-body');

  const fileUrl = `sessions/${activeId}/artifacts/${encodeURIComponent(a.name)}`;

  if (isImage) {
    body.innerHTML = `<img src="${fileUrl}" style="max-width:100%; max-height:80vh; object-fit:contain; border-radius:var(--radius); border:1px solid var(--border);" alt="${esc(a.name)}" />`;
  } else if (isAudio) {
    body.innerHTML = `<audio controls src="${fileUrl}" style="width:100%; margin-top:20px;"></audio>`;
  } else if (isVideo) {
    body.innerHTML = `<video controls src="${fileUrl}" style="max-width:100%; max-height:80vh; border-radius:var(--radius); border:1px solid var(--border);"></video>`;
  } else {
    body.innerHTML = renderMd(a.content || '');
  }

  document.getElementById('js-preview').classList.add('open');
  document.getElementById('js-preview-back').focus();
}

document.getElementById('js-preview-back').addEventListener('click', () => {
  document.getElementById('js-preview').classList.remove('open');
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.getElementById('js-preview').classList.remove('open');
});

/* ── Helpers ───────────────────────────────────────────────────── */
function el(tag, cls) { const e = document.createElement(tag); if(cls) e.className = cls; return e; }
function esc(s) { return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function fmtBytes(b) { if(b<1024) return `${b} B`; if(b<1048576) return `${(b/1024).toFixed(1)} KB`; return `${(b/1048576).toFixed(1)} MB`; }
function artIcon(n) {
  if(n.endsWith('.md')) return '📝'; if(n.endsWith('.py')) return '🐍';
  if(n.endsWith('.json')) return '📋'; if(n.endsWith('.img')) return '🖼️';
  if(n.endsWith('.html')) return '🌐'; return '📄';
}
function emptyHtml(icon, msg) { return `<div class="empty-state"><span class="empty-icon" aria-hidden="true">${icon}</span><div>${esc(msg)}</div></div>`; }
function renderMd(md) {
  if (!md) return "";
  let raw = md.replace(/\r\n/g, "\n");
  raw = raw.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const codeBlocks = [];
  const inlineCodes = [];
  const links = [];
  raw = raw.replace(/```(\w*)\n([\s\S]*?)```/g, (match, lang, code) => {
    const id = `CODEBLOCKPLACEHOLDER${codeBlocks.length}`;
    const cleanCode = code.trim();
    const langLabel = lang ? lang.toUpperCase() : 'CODE';
    codeBlocks.push(`
      <div class="code-block-container">
        <div class="code-block-header">
          <span class="code-block-lang">${langLabel}</span>
        </div>
        <pre><code class="language-${lang}">${cleanCode}</code></pre>
      </div>
    `);
    return id;
  });
  raw = raw.replace(/`([^`\n]+)`/g, (match, code) => {
    const id = `INLINECODEPLACEHOLDER${inlineCodes.length}`;
    inlineCodes.push(`<code>${code}</code>`);
    return id;
  });
  raw = raw.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, text, url) => {
    const id = `LINKPLACEHOLDER${links.length}`;
    links.push(`<a href="${url}" target="_blank" rel="noopener noreferrer">${text}</a>`);
    return id;
  });
  const lines = raw.split("\n");
  const blocks = [];
  let inList = null;
  let inTable = false;
  let tableHeaders = [];
  let tableRows = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      const cells = trimmed.split("|").slice(1, -1).map(c => c.trim());
      const isSep = cells.every(c => /^:?-+:?$/.test(c));
      if (isSep) continue;
      if (!inTable) {
        inTable = true;
        tableHeaders = cells;
      } else {
        tableRows.push(cells);
      }
      continue;
    } else if (inTable) {
      blocks.push(renderTable(tableHeaders, tableRows));
      inTable = false;
      tableHeaders = [];
      tableRows = [];
    }
    const isBullet = /^(?:-|\*|\+)\s+(.+)/.test(trimmed);
    const isNum = /^\d+\.\s+(.+)/.test(trimmed);
    if (inList === 'ul' && !isBullet) {
      blocks.push("</ul>");
      inList = null;
    } else if (inList === 'ol' && !isNum) {
      blocks.push("</ol>");
      inList = null;
    }
    if (/^(?:---|\*\*\*|___)$/.test(trimmed)) {
      blocks.push("<hr>");
      continue;
    }
    const headingMatch = line.match(/^(#{1,6})\s+(.+)/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const text = headingMatch[2].trim();
      blocks.push(`<h${level}>${text}</h${level}>`);
      continue;
    }
    if (line.startsWith("&gt;")) {
      const text = line.substring(4).trim();
      blocks.push(`<blockquote>${text}</blockquote>`);
      continue;
    }
    if (isBullet) {
      const content = trimmed.replace(/^(?:-|\*|\+)\s+/, "");
      if (inList !== 'ul') {
        blocks.push("<ul>");
        inList = 'ul';
      }
      blocks.push(`<li>${content}</li>`);
      continue;
    }
    if (isNum) {
      const content = trimmed.replace(/^\d+\.\s+/, "");
      if (inList !== 'ol') {
        blocks.push("<ol>");
        inList = 'ol';
      }
      blocks.push(`<li>${content}</li>`);
      continue;
    }
    if (trimmed === "") continue;
    blocks.push(`<p>${line}</p>`);
  }
  if (inTable) blocks.push(renderTable(tableHeaders, tableRows));
  if (inList === 'ul') blocks.push("</ul>");
  if (inList === 'ol') blocks.push("</ol>");
  let html = blocks.join("\n");
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__(.*?)__/g, "<strong>$1</strong>");
  html = html.replace(/\*(.*?)\*/g, "<em>$1</em>");
  html = html.replace(/_(.*?)_/g, "<em>$1</em>");
  for (let i = codeBlocks.length - 1; i >= 0; i--) {
    html = html.split(`CODEBLOCKPLACEHOLDER${i}`).join(codeBlocks[i]);
  }
  for (let i = inlineCodes.length - 1; i >= 0; i--) {
    html = html.split(`INLINECODEPLACEHOLDER${i}`).join(inlineCodes[i]);
  }
  for (let i = links.length - 1; i >= 0; i--) {
    html = html.split(`LINKPLACEHOLDER${i}`).join(links[i]);
  }
  return html;
}
function renderTable(headers, rows) {
  let hHtml = "";
  for (const h of headers) hHtml += `<th>${h}</th>`;
  let rHtml = "";
  for (const r of rows) {
    rHtml += "<tr>";
    for (const cell of r) rHtml += `<td>${cell}</td>`;
    rHtml += "</tr>";
  }
  return `<table><thead><tr>${hHtml}</tr></thead><tbody>${rHtml}</tbody></table>`;
}
function updateJumpWidget() {
  const widget = document.getElementById('js-jump-widget');
  const info = document.getElementById('js-jump-info');
  const activeTab = document.querySelector('.tab-btn.active')?.dataset.tab;
  if (activeTab === 'transcript' && userSteps.length > 0) {
    widget.classList.remove('hidden');
    if (currentUserStepIndex === -1) {
      info.textContent = `0 / ${userSteps.length}`;
    } else {
      info.textContent = `${currentUserStepIndex + 1} / ${userSteps.length}`;
    }
  } else {
    widget.classList.add('hidden');
  }
}

function jumpToUserStep(index) {
  if (userSteps.length === 0) return;
  currentUserStepIndex = Math.max(0, Math.min(index, userSteps.length - 1));
  const target = userSteps[currentUserStepIndex];
  if (target) {
    document.querySelectorAll('.step-bubble').forEach(b => b.classList.remove('pulse-active'));
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const bubble = target.querySelector('.step-bubble');
    if (bubble) {
      bubble.classList.add('pulse-active');
      setTimeout(() => { bubble.classList.remove('pulse-active'); }, 2500);
    }
    updateJumpWidget();
  }
}

function jumpNext() {
  if (userSteps.length === 0) return;
  jumpToUserStep(currentUserStepIndex + 1);
}

function jumpPrev() {
  if (userSteps.length === 0) return;
  if (currentUserStepIndex === -1) {
    jumpToUserStep(userSteps.length - 1);
  } else {
    jumpToUserStep(currentUserStepIndex - 1);
  }
}

document.getElementById('js-jump-next').addEventListener('click', jumpNext);
document.getElementById('js-jump-prev').addEventListener('click', jumpPrev);

document.addEventListener('keydown', e => {
  if (document.activeElement.tagName === 'INPUT' || 
      document.activeElement.tagName === 'TEXTAREA' ||
      document.getElementById('js-preview').classList.contains('open')) {
    return;
  }
  if (e.key.toLowerCase() === 'j') {
    e.preventDefault();
    jumpNext();
  }
  if (e.key.toLowerCase() === 'k') {
    e.preventDefault();
    jumpPrev();
  }
});
let activeMatches = [];
let currentMatchIndex = -1;

function highlightTextNodes(root, query) {
  if (!query) return [];
  const matches = [];
  const regex = new RegExp(`(${escapeRegExp(query)})`, 'gi');
  
  function walk(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.nodeValue;
      if (regex.test(text)) {
        const frag = document.createDocumentFragment();
        const parts = text.split(regex);
        for (const part of parts) {
          if (regex.test(part)) {
            const mark = document.createElement('mark');
            mark.className = 'search-match';
            mark.textContent = part;
            frag.appendChild(mark);
            matches.push(mark);
          } else {
            frag.appendChild(document.createTextNode(part));
          }
        }
        node.parentNode.replaceChild(frag, node);
      }
    } else if (node.nodeType === Node.ELEMENT_NODE && node.tagName !== 'MARK' && node.tagName !== 'SCRIPT' && node.tagName !== 'STYLE' && node.tagName !== 'TEXTAREA' && node.tagName !== 'INPUT') {
      const children = Array.from(node.childNodes);
      for (const child of children) {
        walk(child);
      }
    }
  }
  
  walk(root);
  return matches;
}

function escapeRegExp(string) {
  return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function filterSessions(query) {
  const q = query.toLowerCase().trim();
  const list = document.getElementById('js-list');
  let visibleCount = 0;
  
  sessions.forEach(s => {
    const matchesTitle = s.firstMsg.toLowerCase().includes(q);
    const matchesDate = s.date.toLowerCase().includes(q);
    const matchesTranscript = s.transcript.toLowerCase().includes(q);
    const matches = !q || matchesTitle || matchesDate || matchesTranscript;
    
    const btn = list.querySelector(`.session-btn[data-id="${s.id}"]`);
    if (btn) {
      if (matches) {
        btn.style.display = 'block';
        visibleCount++;
      } else {
        btn.style.display = 'none';
      }
    }
  });
  
  document.getElementById('js-count').textContent = `${visibleCount} session${visibleCount !== 1 ? 's' : ''}`;
}

function performSearch() {
  const query = document.getElementById('js-search-input').value;
  const countBadge = document.getElementById('js-search-count');
  const clearBtn = document.getElementById('js-search-clear');
  
  clearSearchHighlights();
  filterSessions(query);
  
  if (!query) {
    countBadge.classList.add('hidden');
    clearBtn.classList.add('hidden');
    activeMatches = [];
    currentMatchIndex = -1;
    return;
  }
  
  clearBtn.classList.remove('hidden');
  
  const container = document.getElementById('panel-transcript');
  activeMatches = highlightTextNodes(container, query);
  currentMatchIndex = -1;
  
  if (activeMatches.length > 0) {
    countBadge.classList.remove('hidden');
    updateSearchCount();
  } else {
    countBadge.classList.add('hidden');
  }
}

function clearSearchHighlights() {
  if (activeId) {
    const s = sessions.find(x => x.id === activeId);
    if (s) {
      renderTranscript(s.transcript, document.getElementById('panel-transcript'));
    }
  }
}

function updateSearchCount() {
  const badge = document.getElementById('js-search-count');
  if (activeMatches.length > 0) {
    badge.textContent = `${currentMatchIndex === -1 ? 0 : currentMatchIndex + 1}/${activeMatches.length}`;
  } else {
    badge.textContent = '0/0';
  }
}

function jumpToMatch(index) {
  if (activeMatches.length === 0) return;
  
  if (currentMatchIndex !== -1 && activeMatches[currentMatchIndex]) {
    activeMatches[currentMatchIndex].classList.remove('current');
  }
  
  currentMatchIndex = (index + activeMatches.length) % activeMatches.length;
  
  const target = activeMatches[currentMatchIndex];
  if (target) {
    target.classList.add('current');
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    updateSearchCount();
  }
}

// Search Bindings
const searchInput = document.getElementById('js-search-input');
searchInput.addEventListener('input', performSearch);

document.getElementById('js-search-clear').addEventListener('click', () => {
  searchInput.value = '';
  performSearch();
  searchInput.focus();
});

searchInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    if (e.shiftKey) {
      jumpToMatch(currentMatchIndex - 1);
    } else {
      jumpToMatch(currentMatchIndex + 1);
    }
  } else if (e.key === 'Escape') {
    searchInput.value = '';
    performSearch();
    searchInput.blur();
  }
});

document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement !== searchInput && 
      document.activeElement.tagName !== 'INPUT' && 
      document.activeElement.tagName !== 'TEXTAREA') {
    e.preventDefault();
    searchInput.focus();
    searchInput.select();
  }
});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# SyncState — persistent change tracking
# ---------------------------------------------------------------------------

class SyncState:
    """Tracks file mtimes and sizes to enable incremental syncs.

    Stores state in a JSON file so that unchanged conversations are
    skipped on subsequent runs.
    """

    def __init__(self, state_path: Path) -> None:
        self._path = state_path
        self._data: dict[str, Any] = {"files": {}}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {"files": {}}

    def has_changed(self, file_path: Path) -> bool:
        """Return True if the file has been modified since last sync."""
        if not file_path.exists():
            return False
        key = str(file_path)
        stat = file_path.stat()
        current_mtime = stat.st_mtime
        current_size = stat.st_size
        previous = self._data["files"].get(key)
        if previous is None:
            return True
        return (
            current_mtime != previous.get("mtime")
            or current_size != previous.get("size")
        )

    def mark_synced(self, file_path: Path) -> None:
        """Record a file's current mtime and size as the synced baseline."""
        if not file_path.exists():
            return
        stat = file_path.stat()
        self._data["files"][str(file_path)] = {
            "mtime": stat.st_mtime,
            "size": stat.st_size,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }

    def save(self) -> None:
        """Persist state to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))


# ---------------------------------------------------------------------------
# GeminiSyncer — core orchestrator
# ---------------------------------------------------------------------------

class GeminiSyncer:
    """Discovers, converts, and mirrors Gemini IDE artifacts for a workspace."""

    def __init__(self, workspace: Path, force: bool = False) -> None:
        self.workspace = workspace.resolve()
        self.output_dir = self.workspace / OUTPUT_DIR_NAME
        self.force = force
        self.state = SyncState(self.output_dir / STATE_FILENAME)

    # -- Discovery ----------------------------------------------------------

    def discover_conversations(self) -> list[str]:
        """Find all conversation IDs linked to this workspace.

        Scans transcript JSONL files for references to the workspace path.
        This is the most reliable method since transcripts contain the
        full workspace path in user metadata.
        """
        workspace_str = str(self.workspace)
        conv_ids: set[str] = set()

        # Scan all transcript files for workspace path references
        for transcript_path in BRAIN_DIR.glob(
            "*/.system_generated/logs/transcript.jsonl"
        ):
            try:
                content = transcript_path.read_text(errors="replace")
                if workspace_str in content:
                    conv_id = transcript_path.parent.parent.parent.name
                    conv_ids.add(conv_id)
            except OSError:
                continue

        # Cross-check: find any DB files whose IDs match discovered transcripts
        # but also check for orphan DBs that might reference this workspace
        for db_path in CONVERSATIONS_DIR.glob("*.db"):
            conv_id = db_path.stem
            if conv_id in conv_ids:
                continue
            transcript = (
                BRAIN_DIR
                / conv_id
                / ".system_generated"
                / "logs"
                / "transcript.jsonl"
            )
            if transcript.exists():
                try:
                    content = transcript.read_text(errors="replace")
                    if workspace_str in content:
                        conv_ids.add(conv_id)
                except OSError:
                    continue

        print(
            f"📍 Discovered {len(conv_ids)} conversation(s) "
            f"for workspace: {self.workspace.name}"
        )
        return sorted(conv_ids)

    # -- Source file collection ---------------------------------------------

    def _source_files(self, conv_id: str) -> list[Path]:
        """Collect all source files for a given conversation ID."""
        files: list[Path] = []

        # Database files
        for suffix in (".db", ".db-wal", ".db-shm"):
            f = CONVERSATIONS_DIR / f"{conv_id}{suffix}"
            if f.exists():
                files.append(f)

        # Transcript files
        logs_dir = BRAIN_DIR / conv_id / ".system_generated" / "logs"
        for name in ("transcript.jsonl", "transcript_full.jsonl"):
            f = logs_dir / name
            if f.exists():
                files.append(f)

        # Brain artifacts (non-system files)
        brain = BRAIN_DIR / conv_id
        if brain.exists():
            for item in brain.iterdir():
                if item.name != ".system_generated" and item.is_file():
                    files.append(item)

        return files

    def _conversation_changed(self, conv_id: str) -> bool:
        """Check if any source file for this conversation has been modified."""
        if self.force:
            return True
        return any(self.state.has_changed(f) for f in self._source_files(conv_id))

    # -- Transcript Conversion ----------------------------------------------

    def _clean_user_content(self, content: str) -> str:
        """Extract the clean user message, stripping system metadata tags."""
        match = _USER_REQUEST.search(content)
        if match:
            return match.group(1).strip()
        cleaned = _META_TAGS.sub("", content)
        return cleaned.strip()

    def _format_tool_calls(self, tool_calls: list[dict]) -> str:
        """Render tool calls as a collapsible markdown section."""
        if not tool_calls:
            return ""

        lines = [
            f"\n<details>",
            f"<summary>🔧 Tool Calls ({len(tool_calls)})</summary>",
            "",
        ]

        for tc in tool_calls:
            name = tc.get("name", "unknown_tool")
            args = tc.get("arguments", tc.get("args", {}))
            lines.append(f"**`{name}`**")

            if isinstance(args, dict):
                compact: dict[str, Any] = {}
                for k, v in args.items():
                    sv = str(v)
                    compact[k] = sv[:200] + "…" if len(sv) > 200 else v
                lines.append(
                    f"```json\n{json.dumps(compact, indent=2, ensure_ascii=False)}\n```"
                )
            elif isinstance(args, str):
                try:
                    parsed = json.loads(args)
                    lines.append(
                        f"```json\n{json.dumps(parsed, indent=2, ensure_ascii=False)}\n```"
                    )
                except json.JSONDecodeError:
                    lines.append(f"```\n{args[:500]}\n```")

        lines.append("")
        lines.append("</details>")
        lines.append("")
        return "\n".join(lines)

    def convert_transcript(
        self, conv_id: str
    ) -> tuple[str, str | None, str | None]:
        """Convert a JSONL transcript to structured markdown.

        Returns:
            (markdown_content, first_user_message, earliest_timestamp)
        """
        transcript_path = (
            BRAIN_DIR
            / conv_id
            / ".system_generated"
            / "logs"
            / "transcript.jsonl"
        )
        if not transcript_path.exists():
            return "# Transcript\n\n*No transcript file found.*\n", None, None

        steps: list[dict] = []
        for line in transcript_path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                steps.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if not steps:
            return "# Transcript\n\n*Empty transcript.*\n", None, None

        # Gather summary stats
        first_ts = steps[0].get("created_at", "")
        first_user_msg: str | None = None
        user_count = 0
        agent_count = 0

        for s in steps:
            stype = s.get("type", "")
            if stype == "USER_INPUT":
                user_count += 1
                if first_user_msg is None:
                    first_user_msg = self._clean_user_content(
                        s.get("content", "")
                    )
            elif stype == "PLANNER_RESPONSE":
                agent_count += 1

        # ---- Build markdown ----
        md: list[str] = []
        md.append("# 📝 Conversation Transcript")
        md.append("")
        md.append("| Property | Value |")
        md.append("|----------|-------|")
        md.append(f"| **Session ID** | `{conv_id}` |")
        md.append(
            f"| **Date** | {first_ts[:10] if first_ts else 'Unknown'} |"
        )
        md.append(f"| **Total Steps** | {len(steps)} |")
        md.append(f"| **User Messages** | {user_count} |")
        md.append(f"| **Agent Responses** | {agent_count} |")
        md.append("")
        md.append("---")
        md.append("")

        for step in steps:
            idx = step.get("step_index", "?")
            step_type = step.get("type", "UNKNOWN")
            created = step.get("created_at", "")
            content = step.get("content", "")
            tool_calls = step.get("tool_calls", [])
            is_truncated = step.get("is_truncated", False)

            icon, label = _STEP_ICONS.get(step_type, ("❓", step_type))

            # Format timestamp
            ts_display = ""
            if created:
                try:
                    dt = datetime.fromisoformat(
                        created.replace("Z", "+00:00")
                    )
                    ts_display = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                except ValueError:
                    ts_display = created

            # ---- USER_INPUT ----
            if step_type == "USER_INPUT":
                clean = self._clean_user_content(content)
                md.append(f"## Step {idx} — {icon} {label}")
                if ts_display:
                    md.append(f"*{ts_display}*")
                md.append("")
                md.append(clean)
                md.append("")
                md.append("---")
                md.append("")

            # ---- PLANNER_RESPONSE ----
            elif step_type == "PLANNER_RESPONSE":
                md.append(f"## Step {idx} — {icon} {label}")
                if ts_display:
                    md.append(f"*{ts_display}*")
                md.append("")

                display = content
                if len(display) > 5000:
                    display = (
                        display[:5000]
                        + "\n\n*… (truncated — see full transcript)*"
                    )
                md.append(display)

                if is_truncated:
                    md.append(
                        "\n> ⚠️ *This step was truncated in the compact "
                        "transcript. See `transcript_full.jsonl` for the "
                        "complete content.*"
                    )

                if tool_calls:
                    md.append(self._format_tool_calls(tool_calls))

                md.append("")
                md.append("---")
                md.append("")

            # ---- Context / Knowledge — collapsed ----
            elif step_type in ("CONVERSATION_HISTORY", "KNOWLEDGE_ITEMS"):
                md.append("<details>")
                md.append(
                    f"<summary>Step {idx} — {icon} {label} "
                    f"({len(content):,} chars)</summary>"
                )
                md.append("")
                if ts_display:
                    md.append(f"*{ts_display}*")
                    md.append("")
                if len(content) > 2000:
                    md.append(content[:2000])
                    md.append(
                        f"\n*… ({len(content) - 2000:,} more characters)*"
                    )
                else:
                    md.append(content)
                md.append("")
                md.append("</details>")
                md.append("")

            # ---- Everything else — collapsed ----
            else:
                if not content and not tool_calls:
                    continue
                md.append("<details>")
                md.append(f"<summary>Step {idx} — {icon} {label}</summary>")
                md.append("")
                if ts_display:
                    md.append(f"*{ts_display}*")
                    md.append("")
                if content:
                    if len(content) > 1000:
                        md.append(content[:1000])
                        md.append(
                            f"\n*… ({len(content) - 1000:,} more characters)*"
                        )
                    else:
                        md.append(content)
                if tool_calls:
                    md.append(self._format_tool_calls(tool_calls))
                md.append("")
                md.append("</details>")
                md.append("")

        return "\n".join(md), first_user_msg, first_ts

    # -- DB Metadata --------------------------------------------------------

    def extract_db_summary(self, conv_id: str) -> str:
        """Extract metadata from the conversation SQLite database."""
        db_path = CONVERSATIONS_DIR / f"{conv_id}.db"
        if not db_path.exists():
            return "# Conversation Metadata\n\n*No database file found.*\n"

        lines = [
            "# 🗃️ Conversation Metadata",
            "",
            f"**Database**: `{db_path.name}`  ",
            f"**Size**: {db_path.stat().st_size:,} bytes",
            "",
        ]

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # -- trajectory_meta --
            try:
                cursor.execute(
                    "SELECT trajectory_id, cascade_id, trajectory_type, "
                    "source FROM trajectory_meta"
                )
                rows = cursor.fetchall()
                if rows:
                    lines.append("## Trajectory Info")
                    lines.append("")
                    lines.append(
                        "| Trajectory ID | Cascade ID | Type | Source |"
                    )
                    lines.append("|---------------|------------|------|--------|")
                    for tid, cid, ttype, src in rows:
                        tid_s = f"`{(tid or '')[:12]}…`"
                        cid_s = f"`{(cid or '')[:12]}…`"
                        lines.append(
                            f"| {tid_s} | {cid_s} | {ttype} | {src} |"
                        )
                    lines.append("")
            except sqlite3.OperationalError:
                pass

            # -- steps summary --
            try:
                cursor.execute("SELECT COUNT(*) FROM steps")
                total = cursor.fetchone()[0]
                lines.append("## Step Statistics")
                lines.append("")
                lines.append(f"**Total steps**: {total}")
                lines.append("")

                cursor.execute(
                    "SELECT step_type, COUNT(*) FROM steps "
                    "GROUP BY step_type ORDER BY COUNT(*) DESC"
                )
                rows = cursor.fetchall()
                if rows:
                    lines.append("| Step Type (int) | Count |")
                    lines.append("|-----------------|-------|")
                    for stype, count in rows:
                        lines.append(f"| {stype} | {count} |")
                    lines.append("")
            except sqlite3.OperationalError:
                pass

            # -- gen_metadata sizes --
            try:
                cursor.execute("SELECT COUNT(*), SUM(size) FROM gen_metadata")
                row = cursor.fetchone()
                if row and row[0]:
                    total_size = row[1] or 0
                    lines.append("## Generation Metadata")
                    lines.append("")
                    lines.append(f"- **Entries**: {row[0]}")
                    lines.append(
                        f"- **Total payload**: {total_size:,} bytes "
                        f"({total_size / 1024:.1f} KB)"
                    )
                    lines.append("")
            except sqlite3.OperationalError:
                pass

            # -- all tables overview --
            cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' ORDER BY name"
            )
            tables = [r[0] for r in cursor.fetchall()]
            lines.append("## Database Tables")
            lines.append("")
            for table in tables:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
                    count = cursor.fetchone()[0]
                    lines.append(f"- `{table}`: {count} rows")
                except sqlite3.OperationalError:
                    lines.append(f"- `{table}`: *(unreadable)*")
            lines.append("")

            conn.close()
        except sqlite3.Error as e:
            lines.append(f"\n*Error reading database: {e}*\n")

        return "\n".join(lines)

    # -- Artifact & DB Copying ----------------------------------------------

    def copy_brain_artifacts(self, conv_id: str, dest: Path) -> list[str]:
        """Copy non-system brain artifacts. Returns list of copied names."""
        brain = BRAIN_DIR / conv_id
        if not brain.exists():
            return []

        artifacts_dir = dest / "artifacts"
        copied: list[str] = []

        for item in brain.iterdir():
            # Skip system-generated directory
            if item.name == ".system_generated":
                continue
            # Recursively copy scratch directory
            if item.name == "scratch" and item.is_dir():
                scratch_dest = artifacts_dir / "scratch"
                shutil.copytree(item, scratch_dest, dirs_exist_ok=True)
                copied.append("scratch/")
                continue
            if item.is_file():
                # Skip internal metadata sidecar files
                if item.name.endswith(".metadata.json"):
                    continue
                artifacts_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, artifacts_dir / item.name)
                copied.append(item.name)

        return copied

    def copy_raw_db(self, conv_id: str, dest: Path) -> None:
        """Copy raw .db, .db-wal, and .db-shm files as backups."""
        raw_dir = dest / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for suffix in (".db", ".db-wal", ".db-shm"):
            src = CONVERSATIONS_DIR / f"{conv_id}{suffix}"
            if src.exists():
                shutil.copy2(src, raw_dir / src.name)

    # -- Per-conversation sync ----------------------------------------------

    def sync_conversation(self, conv_id: str) -> dict[str, Any] | None:
        """Sync a single conversation. Returns metadata if synced, else None."""
        if not self._conversation_changed(conv_id):
            print(f"  ⏭️  {conv_id[:8]}… (unchanged)")
            return None

        print(f"  🔄 {conv_id[:8]}… syncing")
        session_dir = self.output_dir / "sessions" / conv_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # 1. Convert transcript → markdown
        transcript_md, first_msg, first_ts = self.convert_transcript(conv_id)
        (session_dir / "transcript.md").write_text(transcript_md)

        # 2. Extract DB metadata → markdown
        db_summary = self.extract_db_summary(conv_id)
        (session_dir / "conversation.md").write_text(db_summary)

        # 3. Copy brain artifacts
        artifacts = self.copy_brain_artifacts(conv_id, session_dir)

        # 4. Copy raw DB backup
        self.copy_raw_db(conv_id, session_dir)

        # 5. Mark all source files as synced
        for f in self._source_files(conv_id):
            self.state.mark_synced(f)

        return {
            "conv_id": conv_id,
            "first_message": first_msg,
            "timestamp": first_ts,
            "artifacts": artifacts,
        }

    # -- Index Generation ---------------------------------------------------

    def generate_index(self) -> None:
        """Generate master index.md linking all synced sessions."""
        sessions_dir = self.output_dir / "sessions"
        if not sessions_dir.exists():
            return

        entries: list[dict[str, Any]] = []

        for session_path in sorted(sessions_dir.iterdir()):
            if not session_path.is_dir():
                continue
            conv_id = session_path.name
            first_msg = "—"
            date_str = "—"
            step_count = "—"

            transcript = session_path / "transcript.md"
            if transcript.exists():
                content = transcript.read_text()
                for line in content.splitlines():
                    if "**Date**" in line and "|" in line:
                        parts = line.split("|")
                        if len(parts) >= 3:
                            date_str = parts[2].strip()
                    if "**Total Steps**" in line and "|" in line:
                        parts = line.split("|")
                        if len(parts) >= 3:
                            step_count = parts[2].strip()

                # Extract first user message from the transcript
                marker = "## Step 0 — 👤"
                if marker in content:
                    idx = content.index(marker)
                    after = content[idx + len(marker) :]
                    for msg_line in after.splitlines():
                        msg_line = msg_line.strip()
                        if (
                            msg_line
                            and not msg_line.startswith("*")
                            and not msg_line.startswith("#")
                            and not msg_line.startswith("|")
                            and not msg_line.startswith("---")
                            and msg_line != "User Message"
                        ):
                            first_msg = msg_line[:80]
                            if len(msg_line) > 80:
                                first_msg += "…"
                            break

            # Count artifacts
            artifacts_dir = session_path / "artifacts"
            artifact_count = 0
            if artifacts_dir.exists():
                artifact_count = sum(
                    1 for f in artifacts_dir.iterdir() if f.is_file()
                )

            entries.append(
                {
                    "conv_id": conv_id,
                    "date": date_str,
                    "steps": step_count,
                    "first_msg": first_msg,
                    "artifacts": artifact_count,
                }
            )

        # Sort newest first (entries without dates go last)
        entries.sort(
            key=lambda e: e["date"] if e["date"] != "—" else "0000",
            reverse=True,
        )

        # Build the index
        md: list[str] = []
        md.append("# 🪞 Gemini Workspace Mirror")
        md.append("")
        md.append(f"**Workspace**: `{self.workspace.name}`  ")
        md.append(
            f"**Last synced**: "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  "
        )
        md.append(f"**Total sessions**: {len(entries)}")
        md.append("")
        md.append("---")
        md.append("")
        md.append("## Sessions")
        md.append("")
        md.append(
            "| # | Date | Steps | Artifacts | First Message | Links |"
        )
        md.append(
            "|---|------|-------|-----------|---------------|-------|"
        )

        for i, entry in enumerate(entries, 1):
            t_link = f"[📝](sessions/{entry['conv_id']}/transcript.md)"
            d_link = f"[🗃️](sessions/{entry['conv_id']}/conversation.md)"
            links = f"{t_link} {d_link}"
            md.append(
                f"| {i} | {entry['date']} | {entry['steps']} | "
                f"{entry['artifacts']} | {entry['first_msg']} | {links} |"
            )

        md.append("")
        md.append("---")
        md.append("")
        md.append("*Generated by `sync_gemini.py`*")

        (self.output_dir / "index.md").write_text("\n".join(md))

    # -- Viewer Generation --------------------------------------------------

    def _collect_session_data(self) -> list[dict[str, Any]]:
        """Collect all session data into a list of dicts for embedding."""
        sessions_dir = self.output_dir / "sessions"
        if not sessions_dir.exists():
            return []

        result: list[dict[str, Any]] = []

        for session_path in sorted(sessions_dir.iterdir()):
            if not session_path.is_dir():
                continue
            conv_id = session_path.name

            # Read transcript markdown
            transcript_md = ""
            transcript_file = session_path / "transcript.md"
            if transcript_file.exists():
                transcript_md = transcript_file.read_text(errors="replace")

            # Read conversation (DB metadata) markdown
            conversation_md = ""
            conversation_file = session_path / "conversation.md"
            if conversation_file.exists():
                conversation_md = conversation_file.read_text(errors="replace")

            # Collect artifact file names and sizes
            artifacts: list[dict[str, Any]] = []
            artifacts_dir = session_path / "artifacts"
            if artifacts_dir.exists():
                for item in sorted(artifacts_dir.iterdir()):
                    if item.is_file():
                        entry: dict[str, Any] = {
                            "name": item.name,
                            "size": item.stat().st_size,
                        }
                        # Embed text content for .md and .py files (cap at 1MB to avoid bloated JSON)
                        if item.suffix in (".md", ".py", ".txt", ".json"):
                            if item.stat().st_size < 1024 * 1024:
                                try:
                                    entry["content"] = item.read_text(
                                        errors="replace"
                                    )
                                except OSError:
                                    pass
                        artifacts.append(entry)

            # Pull summary fields from transcript
            first_msg = "—"
            date_str = "—"
            step_count = "—"
            user_count = "—"
            agent_count = "—"

            for line in transcript_md.splitlines():
                if "**Date**" in line and "|" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        date_str = parts[2].strip()
                if "**Total Steps**" in line and "|" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        step_count = parts[2].strip()
                if "**User Messages**" in line and "|" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        user_count = parts[2].strip()
                if "**Agent Responses**" in line and "|" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        agent_count = parts[2].strip()

            marker = "## Step 0 — 👤"
            if marker in transcript_md:
                idx = transcript_md.index(marker)
                after = transcript_md[idx + len(marker):]
                for msg_line in after.splitlines():
                    msg_line = msg_line.strip()
                    if (
                        msg_line
                        and not msg_line.startswith("*")
                        and not msg_line.startswith("#")
                        and not msg_line.startswith("|")
                        and not msg_line.startswith("---")
                        and msg_line != "User Message"
                    ):
                        first_msg = msg_line[:100]
                        break

            result.append({
                "id": conv_id,
                "firstMsg": first_msg,
                "date": date_str,
                "steps": step_count,
                "userCount": user_count,
                "agentCount": agent_count,
                "artifactCount": len(artifacts),
                "transcript": transcript_md,
                "metadata": conversation_md,
                "artifacts": artifacts,
            })

        # Sort newest first
        result.sort(
            key=lambda s: s["date"] if s["date"] != "—" else "0000",
            reverse=True,
        )
        return result

    def generate_viewer(self) -> None:
        """Generate a self-contained viewer.html with all data pre-embedded.

        The viewer reads from window.__GEMINI_DATA__ injected at build time,
        so opening the HTML file requires no folder picker or local server.
        """
        sessions = self._collect_session_data()
        meta = {
            "workspace": self.workspace.name,
            "syncedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "totalSessions": len(sessions),
        }

        data_json = json.dumps(
            {"meta": meta, "sessions": sessions},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        # Escape '<' and '>' characters as Unicode escape sequences to prevent
        # breaking the HTML script block if transcripts contain tags like </script>
        data_json = data_json.replace("<", "\\u003c").replace(">", "\\u003e")

        html = _VIEWER_TEMPLATE.replace("__GEMINI_DATA_JSON__", data_json)
        (self.output_dir / "viewer.html").write_text(html, encoding="utf-8")
        print(f"   🌐 Viewer: {self.output_dir / 'viewer.html'}")


    # -- Full Sync ----------------------------------------------------------

    def sync_all(self) -> None:
        """Run a complete sync: discover → convert → index → viewer → state."""
        print(f"\n🪞 Gemini Workspace Mirror")
        print(f"   Workspace: {self.workspace}")
        print(f"   Output:    {self.output_dir}")
        print()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "sessions").mkdir(exist_ok=True)

        conv_ids = self.discover_conversations()
        if not conv_ids:
            print("   No conversations found for this workspace.")
            return

        synced: list[dict[str, Any]] = []
        for conv_id in conv_ids:
            result = self.sync_conversation(conv_id)
            if result:
                synced.append(result)

        self.generate_index()
        self.generate_viewer()
        self.state.save()

        unchanged = len(conv_ids) - len(synced)
        print(
            f"\n✅ Sync complete — "
            f"{len(synced)} updated, {unchanged} unchanged."
        )
        print(f"   📄 Index:  {self.output_dir / 'index.md'}")


# ---------------------------------------------------------------------------
# Watch Mode — fswatch integration
# ---------------------------------------------------------------------------

def watch_mode(workspace: Path, force: bool = False) -> None:
    """Watch for file changes and re-sync automatically using fswatch.

    Uses a 3-second latency window and an additional 5-second debounce
    so that rapid successive writes don't trigger redundant syncs.
    """
    # Verify fswatch is installed
    try:
        subprocess.run(
            ["fswatch", "--version"],
            capture_output=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("❌ fswatch is not installed. Install it with:")
        if sys.platform == "darwin":
            print("   brew install fswatch")
        elif sys.platform.startswith("linux"):
            print("   sudo apt-get install fswatch  (Debian/Ubuntu) or dnf install fswatch (Fedora)")
        else:
            print("   Please install fswatch for your operating system.")
        sys.exit(1)

    watch_dirs = [str(CONVERSATIONS_DIR), str(BRAIN_DIR)]

    print("👀 Watching for changes…")
    print(
        f"   Monitoring: "
        f"{', '.join(Path(d).name for d in watch_dirs)}"
    )
    print("   Press Ctrl+C to stop.\n")

    # Initial sync
    syncer = GeminiSyncer(workspace, force=force)
    syncer.sync_all()

    cmd = [
        "fswatch",
        "--recursive",
        "--latency",
        "3",
        *watch_dirs,
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    try:
        last_sync = time.time()
        assert proc.stdout is not None
        for _line in proc.stdout:
            now = time.time()
            if now - last_sync < 5:
                continue
            last_sync = now
            print(
                f"\n🔔 Change detected at "
                f"{datetime.now().strftime('%H:%M:%S')}"
            )
            syncer = GeminiSyncer(workspace)
            syncer.sync_all()
    except KeyboardInterrupt:
        print("\n\n👋 Watch mode stopped.")
    finally:
        proc.terminate()
        proc.wait()


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments and run the appropriate sync mode."""
    parser = argparse.ArgumentParser(
        description=(
            "Mirror Antigravity IDE artifacts into a local "
            ".gemini-local/ folder with readable markdown."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python sync_gemini.py                    # One-shot sync (current dir)
  python sync_gemini.py --watch            # Watch mode with auto-sync
  python sync_gemini.py --force            # Force full re-sync
  python sync_gemini.py --workspace /path  # Specify workspace path
        """,
    )
    parser.add_argument(
        "--workspace",
        "-w",
        type=Path,
        default=Path.cwd(),
        help="Workspace directory (default: current directory)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch for changes and auto-sync using fswatch",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force re-sync even if files haven't changed",
    )

    args = parser.parse_args()

    if args.watch:
        watch_mode(args.workspace, force=args.force)
    else:
        syncer = GeminiSyncer(args.workspace, force=args.force)
        syncer.sync_all()


if __name__ == "__main__":
    main()
