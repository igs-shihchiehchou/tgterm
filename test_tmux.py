"""Standalone test of tmuxctl + render (no Telegram needed)."""
import time

import tmuxctl
from render import render_png

S = "tgnb_test_pytest"

tmuxctl.server_init()
if tmuxctl.has_session(S):
    tmuxctl.kill_session(S)
tmuxctl.new_session(S)
time.sleep(0.5)

# 1. basic command + output extraction
out, code, to = tmuxctl.run_command(S, "echo hello", 10)
print("1 basic:", repr(out), "code", code, "timeout", to)
assert out == "hello" and code == 0, "basic failed"

# 2. cwd persistence
tmuxctl.run_command(S, "cd /tmp", 10)
out, code, _ = tmuxctl.run_command(S, "pwd", 10)
print("2 cwd:", repr(out), "code", code)
assert out == "/tmp"

# 3. env persistence
tmuxctl.run_command(S, "export FOO=bar123", 10)
out, code, _ = tmuxctl.run_command(S, "echo $FOO", 10)
print("3 env:", repr(out), "code", code)
assert out == "bar123"

# 4. non-zero exit
out, code, _ = tmuxctl.run_command(S, "ls /nonexistent_xyz 2>&1", 10)
print("4 exit:", repr(out[:50]), "code", code)
assert code != 0

# 5. multiline
out, code, _ = tmuxctl.run_command(S, "printf 'a\\nb\\nc\\n'", 10)
print("5 multiline:", repr(out), "code", code)
assert out == "a\nb\nc"

# 6. timeout
out, code, to = tmuxctl.run_command(S, "sleep 30", 2)
print("6 timeout: timed_out", to, "code", code)
assert to and code is None
tmuxctl.send_key(S, "C-c")
time.sleep(0.3)

# 7. usable after cancel
out, code, _ = tmuxctl.run_command(S, "echo after_cancel", 10)
print("7 after cancel:", repr(out), "code", code)
assert out == "after_cancel"

# 8. render screenshot
colored = tmuxctl.capture(S, history=False, color=True)
png = render_png(colored)
open("/tmp/claude_screen_test.png", "wb").write(png)
print("8 render: png bytes", len(png), "-> /tmp/claude_screen_test.png")
assert png[:8] == b"\x89PNG\r\n\x1a\n"

# 9. raw keystrokes (literal)
tmuxctl.send_literal(S, "echo raw_typed")
time.sleep(0.1)
vis = tmuxctl.capture(S)
print("9 raw literal present:", "raw_typed" in vis)
assert "raw_typed" in vis
tmuxctl.send_key(S, "Enter")

tmuxctl.kill_session(S)
print("ALL DONE")
