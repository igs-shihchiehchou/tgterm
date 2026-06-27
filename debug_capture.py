"""Diagnostic: replicate run_command and dump the RAW tmux capture.

Run on the real machine:  uv run python debug_capture.py
Paste the whole output back.
"""
import time
import tmuxctl

S = "tgnb_debug_capture"

tmuxctl.server_init()
if tmuxctl.has_session(S):
    tmuxctl.kill_session(S)
tmuxctl.new_session(S)
print("session created, waiting for shell...")
time.sleep(1.0)

# what shell is the pane running?
import subprocess
sh = subprocess.run(
    ["tmux", "display-message", "-t", f"={S}", "-p", "#{pane_current_command}"],
    capture_output=True, text=True,
).stdout.strip()
print("pane shell:", repr(sh))

token = "DBG123"
beg, end = f"__BEG_{token}__", f"__END_{token}__"
line = f"echo {beg}; ls; echo {end}:$?"
print("\n--- sending literal line ---")
print(repr(line))
tmuxctl.send_literal(S, line)
tmuxctl.send_key(S, "Enter")

time.sleep(1.5)

print("\n=== capture-pane -p (visible) ===")
print(repr(tmuxctl.capture(S, history=False)))

print("\n=== capture-pane -p -S -5000 (history) ===")
cap = tmuxctl.capture(S, history=True)
print(repr(cap))

print("\n=== line-by-line (history), marking exact marker matches ===")
for i, ln in enumerate(cap.split("\n")):
    tag = ""
    if ln.strip() == beg:
        tag = "  <== BEG exact match"
    if ln.strip().startswith(end):
        tag = "  <== END match"
    print(f"{i:3} | {ln!r}{tag}")

tmuxctl.kill_session(S)
print("\ndone, session killed")
