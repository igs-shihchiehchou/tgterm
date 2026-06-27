"""Thin wrapper around the tmux CLI.

Sessions live in tmux, not in this process, so they survive bot restarts and can
be attached from a real terminal with `tmux attach -t <full-name>`.
"""

from __future__ import annotations

import re
import subprocess
import time
import uuid

PANE_WIDTH = 200
PANE_HEIGHT = 50
HISTORY_LINES = 5000


def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", *args], capture_output=True, text=True
    )


def server_init() -> None:
    """Make new sessions keep plenty of scrollback for output extraction."""
    _tmux("set-option", "-g", "history-limit", str(HISTORY_LINES))


def has_session(full: str) -> bool:
    return _tmux("has-session", "-t", full).returncode == 0


def new_session(full: str) -> None:
    _tmux(
        "new-session", "-d", "-s", full,
        "-x", str(PANE_WIDTH), "-y", str(PANE_HEIGHT),
    )


def kill_session(full: str) -> None:
    _tmux("kill-session", "-t", full)


def list_sessions(prefix: str) -> list[str]:
    res = _tmux("list-sessions", "-F", "#{session_name}")
    if res.returncode != 0:
        return []
    out = []
    for line in res.stdout.splitlines():
        if line.startswith(prefix):
            out.append(line[len(prefix):])
    return out


def capture(full: str, history: bool = False, color: bool = False) -> str:
    args = ["capture-pane", "-t", full, "-p"]
    if color:
        args.append("-e")
    if history:
        args += ["-S", f"-{HISTORY_LINES}"]
    return _tmux(*args).stdout


def send_literal(full: str, text: str) -> None:
    _tmux("send-keys", "-t", full, "-l", "--", text)


def send_key(full: str, key: str) -> None:
    """Send a named key like Enter, Escape, Tab, Space, C-c."""
    _tmux("send-keys", "-t", full, key)


def run_command(full: str, cmd: str, timeout: float) -> tuple[str, int | None, bool]:
    """Run cmd, return (output, exit_code, timed_out).

    Brackets the command with unique markers echoed into the pane, then polls
    the captured scrollback until the end marker (with exit code) appears. The
    output is exactly the lines printed between the two marker lines.
    """
    token = uuid.uuid4().hex[:10]
    beg = f"__BEG_{token}__"
    end = f"__END_{token}__"
    end_re = re.compile(rf"^{end}:(\d+)$")

    send_literal(full, f"echo {beg}; {cmd}; echo {end}:$?")
    send_key(full, "Enter")

    deadline = time.time() + timeout
    while True:
        cap = capture(full, history=True)
        sliced = _slice(cap, beg, end_re)
        if sliced is not None:
            return sliced[0], sliced[1], False
        if time.time() >= deadline:
            return _partial(cap, beg), None, True
        time.sleep(0.25)


def _slice(cap: str, beg: str, end_re: re.Pattern) -> tuple[str, int] | None:
    lines = [ln.rstrip() for ln in cap.split("\n")]
    beg_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == beg:  # the echoed output line, not the typed command
            beg_idx = i
    if beg_idx is None:
        return None
    for j in range(beg_idx + 1, len(lines)):
        m = end_re.match(lines[j].strip())
        if m:
            return "\n".join(lines[beg_idx + 1:j]), int(m.group(1))
    return None


def _partial(cap: str, beg: str) -> str:
    lines = [ln.rstrip() for ln in cap.split("\n")]
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == beg:
            return "\n".join(lines[i + 1:])
    return ""
