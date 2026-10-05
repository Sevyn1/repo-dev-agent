#!/usr/bin/env python3
"""
dev_agent — Generic Repository Development Assistant (with basic image support + rich CLI UX)

Usage:
    python3 dev_agent.py
    python3 dev_agent.py --verbose
    python3 dev_agent.py --quiet
    python3 dev_agent.py --trace
"""

import os
import shlex
from pathlib import Path
import difflib
import shutil
import uuid
import fnmatch
import textwrap
import base64
import itertools
import threading
import time
import sys
import argparse
import traceback

from dotenv import load_dotenv
from agents import Agent, Runner, function_tool, SQLiteSession
from openai import OpenAI

# ============================================================
# GLOBAL CLI SETTINGS (set via argparse in main)
# ============================================================

VERBOSE: bool = False
QUIET: bool = False
SHOW_TRACE: bool = False

# ANSI colors (no external deps)
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

FG_RED = "\033[31m"
FG_GREEN = "\033[32m"
FG_YELLOW = "\033[33m"
FG_BLUE = "\033[34m"
FG_CYAN = "\033[36m"
FG_MAGENTA = "\033[35m"
FG_GRAY = "\033[90m"


def color(text: str, c: str) -> str:
    return f"{c}{text}{RESET}"


# ============================================================
# SPINNER, PROGRESS LOGGER, STAGES, PROGRESS BAR
# ============================================================


class Spinner:
    def __init__(self, label: str = "Working"):
        self.label = label
        self.running = False
        self.thread: threading.Thread | None = None

    def start(self):
        if self.running or QUIET:
            return
        self.running = True
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def _spin(self):
        for c in itertools.cycle("|/-\\"):
            if not self.running:
                break
            sys.stdout.write(f"\r{color(self.label, FG_CYAN)} {color(c, FG_GRAY)}")
            sys.stdout.flush()
            time.sleep(0.1)
        sys.stdout.write("\r" + " " * (len(self.label) + 8) + "\r")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()


class ProgressLogger:
    @staticmethod
    def info(msg: str):
        if QUIET:
            return
        print(color(f"ℹ {msg}", FG_BLUE))

    @staticmethod
    def step(msg: str):
        if QUIET:
            return
        print(color(f"\n🔹 {msg}", FG_CYAN))

    @staticmethod
    def success(msg: str):
        if QUIET:
            return
        print(color(f"✅ {msg}", FG_GREEN))

    @staticmethod
    def warn(msg: str):
        if QUIET:
            return
        print(color(f"⚠ {msg}", FG_YELLOW))

    @staticmethod
    def error(msg: str):
        # Errors should always show, even in quiet mode
        print(color(f"\n❌ ERROR: {msg}", FG_RED))

    @staticmethod
    def debug(msg: str):
        if not VERBOSE or QUIET:
            return
        print(color(f"🐛 {msg}", FG_GRAY))


def print_stage(title: str):
    if QUIET:
        return
    bar = "━" * 60
    print(color(f"\n{bar}", FG_MAGENTA))
    print(color(f"▶ {title}", FG_MAGENTA + BOLD))
    print(color(bar, FG_MAGENTA))


class LoadingStage:
    """
    Context manager for a timed spinner + stage log.

    Use for higher-level operations (agent run, shell, file IO).
    """

    def __init__(self, label: str, show_stage: bool = False):
        self.label = label
        self.spinner = Spinner(label)
        self.start_time: float | None = None
        self.show_stage = show_stage

    def __enter__(self):
        if self.show_stage:
            print_stage(self.label)
        else:
            ProgressLogger.step(self.label)
        self.start_time = time.time()
        self.spinner.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.spinner.stop()
        elapsed = round(time.time() - (self.start_time or time.time()), 2)

        if exc_type is None:
            ProgressLogger.success(f"{self.label} completed in {elapsed}s")
        else:
            ProgressLogger.error(f"{self.label} failed after {elapsed}s: {exc_val}")
            if SHOW_TRACE or VERBOSE:
                traceback.print_exception(exc_type, exc_val, exc_tb)
            else:
                ProgressLogger.info(
                    "Run again with --trace or --verbose for full stack trace."
                )
        # Do NOT suppress exception
        return False


class ProgressBar:
    """Simple text progress bar based on a known maximum."""

    def __init__(self, total: int, label: str = "Progress"):
        self.total = max(total, 1)
        self.label = label
        self.current = 0
        self.width = 20

    def update(self, current: int):
        if QUIET:
            return
        self.current = current
        frac = min(max(self.current / self.total, 0.0), 1.0)
        filled = int(frac * self.width)
        bar = "█" * filled + "-" * (self.width - filled)
        percent = int(frac * 100)
        sys.stdout.write(f"\r{color(self.label, FG_CYAN)} [{bar}] {percent:3d}%")
        sys.stdout.flush()

    def finish(self):
        if QUIET:
            return
        self.update(self.total)
        sys.stdout.write("\n")
        sys.stdout.flush()


# ============================================================
# ENV + CLIENT
# ============================================================

client = None

# ============================================================
# PROJECT IDENTIFICATION & STORAGE
# ============================================================

PROJECT_ROOT = os.path.abspath(os.getcwd())
PROJECT_KEY = os.path.basename(PROJECT_ROOT.rstrip(os.sep)) or "unknown_project"

STORAGE_DIR = os.path.join(PROJECT_ROOT, ".dev_agent")

SESSION_PATH = os.path.join(STORAGE_DIR, f"{PROJECT_KEY}_session.sqlite")
PROMPT_PATH = os.path.join(STORAGE_DIR, f"{PROJECT_KEY}_prompt.txt")
HISTORY_PATH = os.path.join(STORAGE_DIR, f"{PROJECT_KEY}_history.txt")


def setup_history():
    """
    Persistent command history across sessions (per project).
    Arrow keys to recall past commands, like a mini REPL.
    """
    try:
        import readline  # Only works on Unix-like systems, including macOS
    except ImportError:
        ProgressLogger.debug("readline not available; history disabled.")
        return

    try:
        if os.path.exists(HISTORY_PATH):
            readline.read_history_file(HISTORY_PATH)
    except Exception:
        ProgressLogger.debug("Failed to load history file.")

    import atexit

    def save_history():
        try:
            readline.write_history_file(HISTORY_PATH)
        except Exception:
            pass

    atexit.register(save_history)


# ============================================================
# PROJECT PROMPT
# ============================================================


def ensure_project_prompt_file() -> None:
    if os.path.exists(PROMPT_PATH):
        return

    template = (
        textwrap.dedent(f"""
    # Project Prompt for {PROJECT_KEY}

    Describe this project for the agent. This file is NOT code; it's context.

    Suggestions:
    - What this repo is (app / backend / library / etc.)
    - Tech stack (SwiftUI iOS app, Spring Boot backend, React front-end, etc.)
    - Architecture and important conventions (MVVM, Redux, Clean Architecture, etc.)
    - Any rules to follow (no breaking public APIs, keep style consistent, etc.)
    - Anything "weird" about this project the agent should know.

    The more you fill this in, the better its decisions will be.
    """).strip()
        + "\n"
    )

    with open(PROMPT_PATH, "w", encoding="utf-8") as f:
        f.write(template)


def load_project_prompt() -> str:
    ensure_project_prompt_file()
    try:
        with open(PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


# ============================================================
# PATH NORMALIZATION
# ============================================================


def _normalize_path(path: str) -> str:
    root = Path(PROJECT_ROOT).resolve()
    candidate = (root / path).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise ValueError("Path escapes project root; not allowed.") from None
    blocked = {".git", ".dev_agent", "id_rsa", "id_ed25519", "credentials.json"}
    if any(
        part in blocked or part == ".env" or part.startswith(".env.")
        for part in relative.parts
    ) or candidate.suffix.lower() in {".pem", ".key"}:
        raise ValueError("Credential or internal-state paths are not allowed.")
    return str(relative)


# ============================================================
# TOOLS
# ============================================================


def list_project_files(
    subdir: str = ".", pattern: str = "*", max_results: int = 200
) -> list[str]:
    """List bounded, non-sensitive project files without following symlinks."""
    if not 1 <= max_results <= 5000:
        raise ValueError("max_results must be 1–5000")
    relative = _normalize_path(subdir)
    base = Path(PROJECT_ROOT) / relative
    if not base.is_dir():
        return []
    ignored = {"node_modules", "DerivedData", "build", "dist", "Pods", "__pycache__"}
    results = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not d.startswith(".")
            and d not in ignored
            and not Path(dirpath, d).is_symlink()
        )
        for name in sorted(filenames):
            candidate = Path(dirpath, name)
            if candidate.is_symlink():
                continue
            try:
                rel = _normalize_path(str(candidate))
            except ValueError:
                continue
            if fnmatch.fnmatch(rel, pattern):
                results.append(rel)
                if len(results) >= max_results:
                    return results
    return results


def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Read a bounded portion of a UTF-8 text file within the workspace."""
    rel = _normalize_path(path)
    full = Path(PROJECT_ROOT) / rel
    if not full.is_file():
        raise FileNotFoundError(f"File not found: {rel}")
    if full.stat().st_size > 128 * 1024:
        raise ValueError("Text file exceeds the 128 KiB read limit")
    if start_line < 1 or (end_line is not None and end_line < start_line):
        raise ValueError("Invalid line range")
    lines = full.read_text(encoding="utf-8").splitlines(keepends=True)
    stop = min(
        end_line if end_line is not None else start_line + 1999, start_line + 1999
    )
    return "".join(lines[start_line - 1 : stop])


def _confirm_action(description: str) -> bool:
    try:
        return (
            input(f"\n{description}\nApprove this action? [y/N] ").strip().lower()
            == "y"
        )
    except (EOFError, KeyboardInterrupt):
        return False


def apply_patch(
    path: str, original_snippet: str, updated_snippet: str, occurrences: int = 1
) -> str:
    """Review and apply a bounded replacement, backing up the original first."""
    rel = _normalize_path(path)
    full = Path(PROJECT_ROOT) / rel
    content = read_file(path)
    # Read the entire bounded text after read_file has validated path and size.
    content = full.read_text(encoding="utf-8")
    if not original_snippet:
        raise ValueError("Empty original snippet is not allowed")
    matches = content.count(original_snippet)
    if matches == 0:
        raise ValueError("Original snippet not found; file unchanged")
    if occurrences < 0 or occurrences > 1000:
        raise ValueError("occurrences must be 0 (all) or 1–1000")
    if occurrences == 1 and matches > 1:
        raise ValueError(
            "Snippet is ambiguous; provide more context or an explicit replacement count"
        )
    count = matches if occurrences == 0 else min(occurrences, matches)
    new_content = content.replace(original_snippet, updated_snippet, count)
    if new_content == content:
        return "No change required."
    diff = "".join(
        difflib.unified_diff(
            content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=rel,
            tofile=rel,
        )
    )
    if len(diff) > 12000:
        raise ValueError("Patch is too large for review; split it into smaller changes")
    if not _confirm_action(f"Proposed patch to {rel}:\n{diff}"):
        return "Patch declined; file unchanged."
    # Guard against edits made while the operator reviewed the proposal.
    if full.read_text(encoding="utf-8") != content:
        raise ValueError("File changed during review; refusing to overwrite")
    backup_dir = Path(PROJECT_ROOT) / ".dev_agent" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{uuid.uuid4().hex}-{full.name}"
    shutil.copy2(full, backup)
    full.write_text(new_content, encoding="utf-8")
    return f"Patched {rel}: {count} replacement(s). Backup: {backup.relative_to(PROJECT_ROOT)}"


def run_shell(command: str, timeout_seconds: int = 30) -> str:
    """Run an operator-approved shell command. This is not a sandbox."""
    if not command.strip() or not 1 <= timeout_seconds <= 120:
        raise ValueError("Provide a command and a timeout of 1–120 seconds")
    if not _confirm_action(f"Execute in {PROJECT_ROOT}:\n{command}"):
        return "Command declined; nothing executed."
    import subprocess

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if len(output) > 4000:
        output = output[:4000] + "\n...[truncated]..."
    return f"Exit code: {result.returncode}\n{output or '(no output)'}"


def analyze_image(
    path: str, task: str = "Describe relevant layout issues or error messages."
) -> str:
    """Send an operator-approved image with the Responses API's image schema."""
    rel = _normalize_path(path)
    full = Path(PROJECT_ROOT) / rel
    if not full.is_file():
        raise FileNotFoundError(f"Image file not found: {rel}")
    if full.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("Image exceeds the 5 MiB limit")
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(full.suffix.lower())
    if not mime:
        raise ValueError("Use PNG, JPEG, WebP or GIF")
    if not _confirm_action(
        f"Send {rel} to OpenAI for image analysis? This may incur API usage charges."
    ):
        return "Image analysis declined; nothing sent."
    global client
    if client is None:
        client = OpenAI(timeout=30)
    b64_data = base64.b64encode(full.read_bytes()).decode("ascii")
    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": task},
                    {
                        "type": "input_image",
                        "image_url": f"data:{mime};base64,{b64_data}",
                    },
                ],
            }
        ],
    )
    return response.output_text.strip() or "The model returned no text."


# ============================================================
# AGENT DEFINITION
# ============================================================

BASE_INSTRUCTIONS = textwrap.dedent("""
    You are a senior engineering assistant working on the CURRENT repository only.

    CONTEXT:
    - You are always running inside the ROOT of the git repo.
    - The user may be working with any tech stack (Swift, SwiftUI, Kotlin, Java, JS, TS, Python, etc.).
    - The repository structure and conventions are NOT known in advance; you must discover them via tools.
    - You have access to an analyze_image tool that can inspect screenshots or UI images.

    GOALS:
    - Understand the user's intent in concrete terms: which files, modules, and flows are affected.
    - Use list_project_files to discover where relevant code lives.
    - Use read_file to inspect specific files before suggesting or applying changes.
    - Use apply_patch for precise edits; avoid rewriting entire files unless explicitly asked.
    - When the user refers to a screenshot or an image path (e.g. *.png, *.jpg),
      call analyze_image first and use its output to guide your diagnosis or code changes.
    - Prefer minimal, focused patches that preserve existing style and structure.
    - After edits, clearly explain:
        - Which files you touched
        - What changed
        - What the user should test or run (e.g. 'build the iOS app', 'run pytest', etc.).

    RULES:
    - NEVER guess file contents: always call read_file before patching.
    - NEVER modify files outside the project root.
    - When in doubt about where something lives, first call list_project_files
      with a relevant pattern like '*.swift', '*App*', '*View*', '*Controller*', '*.kt', '*.js', etc.
    - For multi-file changes (e.g., new screen, new API flow), perform multiple read/patch cycles and narrate each step.
    - Keep responses concise but specific. Show code snippets for key changes.
    - You can use run_shell to run commands like:
        - 'ls'
        - 'ls <subdir>'
        - 'swift --version', 'python --version'
        - project-specific commands the user suggests (tests, formatters, etc.).

    INTERACTION STYLE:
    - Think and act like a pragmatic senior engineer.
    - First, restate what you think the user wants in terms of concrete changes.
    - Then describe your plan in 1–3 bullet points.
    - Then execute with tools.
    - Finally, summarize what you did and what the user should verify.

    - Patches, shell commands, and image transmission require operator approval.
    - Tool file access is bounded, but shell execution is not a sandbox.
""")


def build_instructions() -> str:
    project_prompt = load_project_prompt()
    if project_prompt.strip():
        return (
            BASE_INSTRUCTIONS + "\n\n" + "PROJECT-SPECIFIC CONTEXT:\n" + project_prompt
        )
    else:
        return BASE_INSTRUCTIONS


agent = None
session = None


def initialize_runtime():
    global agent, session
    os.makedirs(STORAGE_DIR, exist_ok=True)
    agent = Agent(
        name=f"{PROJECT_KEY}_dev_agent",
        instructions=build_instructions(),
        model="gpt-4.1-mini",
        tools=[
            function_tool(tool)
            for tool in [
                list_project_files,
                read_file,
                apply_patch,
                run_shell,
                analyze_image,
            ]
        ],
    )
    session = SQLiteSession(PROJECT_KEY, db_path=SESSION_PATH)


def _extract_image_path(text: str) -> str | None:
    """Recognize supported, existing images inside the selected workspace."""
    try:
        candidates = shlex.split(text)
    except ValueError:
        candidates = text.split()
    for token in candidates:
        token = token.strip(" '\"")
        if Path(token).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            continue
        try:
            rel = _normalize_path(token)
        except ValueError:
            continue
        if (Path(PROJECT_ROOT) / rel).is_file():
            return rel
    return None


def main():
    global VERBOSE, QUIET, SHOW_TRACE

    parser = argparse.ArgumentParser(
        description="dev_agent — per-repo development assistant"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Verbose logging (debug, traces)."
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Minimal output (only errors and final answer).",
    )
    parser.add_argument(
        "--trace", action="store_true", help="Show full stack traces on errors."
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="List project files without model calls or state creation.",
    )
    args = parser.parse_args()
    if args.check:
        print("Local tool check: no model calls")
        for path in list_project_files(max_results=20):
            print(path)
        return
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error(
            "Set OPENAI_API_KEY for live mode, or use --check for an offline check"
        )
    initialize_runtime()
    VERBOSE = args.verbose
    QUIET = args.quiet
    SHOW_TRACE = args.trace or args.verbose

    setup_history()

    if not QUIET:
        print(color("🔧 dev_agent (with rich CLI feedback)", FG_GREEN + BOLD))
        print(f"📁 Project root: {PROJECT_ROOT}")
        print(f"🧠 Session file: {SESSION_PATH}")
        print(f"📝 Project prompt: {PROMPT_PATH}")
        if VERBOSE:
            print(color("Mode: VERBOSE", FG_GRAY))
        print()
        print(
            color("Tip:", FG_CYAN),
            "Edit the project prompt file to teach the agent about this repo.",
        )
        print(
            color("Tip:", FG_CYAN),
            "You can paste an image path (e.g. 'Screenshots/onboarding_bug.png') to analyze a screenshot.",
        )
        print()

    while True:
        try:
            user_input = input(color("You: ", FG_YELLOW)).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if user_input.lower() in {"exit", "quit"}:
            print("Bye.")
            break

        if not user_input:
            continue

        # Auto-wrap image input
        img_path = _extract_image_path(user_input)
        if img_path:
            user_input = (
                f'The user provided a screenshot at path "{img_path}". '
                f"First, call analyze_image on that path to understand the UI or error state. "
                f"Then, based on the analysis, help the user diagnose or fix issues in this repository. "
                f"Original user text: {user_input!r}"
            )

        try:
            print_stage("Agent processing request")
            ProgressLogger.info("⏳ Agent is thinking...")
            with LoadingStage("Running agent", show_stage=False):
                result = Runner.run_sync(agent, user_input, session=session)

            if not QUIET:
                print(color("\n📄 Agent Response:\n", FG_GREEN + BOLD))
            print(result.final_output)
            if not QUIET:
                print("\n" + "-" * 60 + "\n")

        except Exception as e:
            ProgressLogger.error(f"Agent crashed: {e}")
            if SHOW_TRACE:
                traceback.print_exc()
            else:
                ProgressLogger.info(
                    "Run again with --trace or --verbose for full stack trace."
                )


if __name__ == "__main__":
    main()
