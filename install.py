#!/usr/bin/env python3
"""
Universal Installer for profile-audit.
Automatically installs and registers profile-audit across all supported AI coding agents:
- Google Antigravity (~/.gemini/config/skills/profile-audit)
- OpenCode (~/.config/opencode/skills/profile-audit & ~/.config/opencode/command/profile-audit.md)
- Universal Agent Skills (~/.agents/skills/profile-audit)
- Claude Code (~/.claude/skills/profile-audit)
- Cursor / Codex (~/.cursor/skills/profile-audit)
"""

import os
import shutil
import sys
from pathlib import Path

def get_home() -> Path:
    return Path.home()

def install_directory(src: Path, dest: Path) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        return True
    except Exception as e:
        print(f"  [!] Failed to copy to {dest}: {e}", file=sys.stderr)
        return False

def install_file(src_content: str, dest: Path) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src_content, encoding="utf-8")
        return True
    except Exception as e:
        print(f"  [!] Failed to write {dest}: {e}", file=sys.stderr)
        return False

def main():
    root = Path(__file__).resolve().parent
    skill_src = root / "skills" / "profile-audit"
    if not skill_src.exists():
        print(f"Error: {skill_src} not found.", file=sys.stderr)
        sys.exit(1)

    home = get_home()
    print("=" * 60)
    print("  profile-audit Universal Agent Installer")
    print("=" * 60)

    # 1. Google Antigravity & Antigravity CLI
    antigravity_dest = home / ".gemini" / "config" / "skills" / "profile-audit"
    if install_directory(skill_src, antigravity_dest):
        print("  ✓ Google Antigravity: Installed to ~/.gemini/config/skills/profile-audit")

    # 2. Universal Agent Skills (~/.agents/skills)
    agents_dest = home / ".agents" / "skills" / "profile-audit"
    if install_directory(skill_src, agents_dest):
        print("  ✓ Universal Agent Skills: Installed to ~/.agents/skills/profile-audit")

    # 3. OpenCode (~/.config/opencode/skills)
    opencode_dest = home / ".config" / "opencode" / "skills" / "profile-audit"
    if install_directory(skill_src, opencode_dest):
        print("  ✓ OpenCode (Skills): Installed to ~/.config/opencode/skills/profile-audit")

    # 4. OpenCode Slash Commands (~/.config/opencode/command & ~/.opencode/command)
    opencode_cmd_content = """---
description: Profile applications across any language or framework in report-only mode
---

Run the profile-audit skill for $ARGUMENTS.

Read ~/.config/opencode/skills/profile-audit/SKILL.md and execute the profiling runbook.
Observe and measure real execution timings without altering target application source code or databases.
If specific endpoints or functions are specified, execute Targeted Mode and generate PROFILING-AUDIT.md.
Otherwise, execute Full Audit Mode and produce PROFILING-DASHBOARD.html, FINDINGS.md, and FIXES.md.
"""
    cmd_paths = [
        home / ".config" / "opencode" / "command" / "profile-audit.md",
        home / ".config" / "opencode" / "command" / "profile.md",
        home / ".opencode" / "command" / "profile-audit.md",
        home / ".opencode" / "command" / "profile.md",
    ]
    for p in cmd_paths:
        install_file(opencode_cmd_content, p)
    print("  ✓ OpenCode (Slash Commands): Registered /profile-audit and /profile in command menus")

    # 5. Claude Code (~/.claude/skills)
    claude_dest = home / ".claude" / "skills" / "profile-audit"
    if install_directory(skill_src, claude_dest):
        print("  ✓ Claude Code: Installed to ~/.claude/skills/profile-audit")

    # 6. Cursor (~/.cursor/skills)
    cursor_dest = home / ".cursor" / "skills" / "profile-audit"
    if install_directory(skill_src, cursor_dest):
        print("  ✓ Cursor: Installed to ~/.cursor/skills/profile-audit")

    print("\n[SUCCESS] profile-audit is installed and active across all your agents!")
    print("You can now invoke `/profile-audit` or ask 'profile this application' in:")
    print("  - OpenCode (now visible in the / autocomplete menu)")
    print("  - Google Antigravity & Antigravity CLI")
    print("  - Claude Code, Codex, and Cursor")

if __name__ == "__main__":
    main()
