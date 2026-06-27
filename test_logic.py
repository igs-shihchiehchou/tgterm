"""Unit tests for the pure logic that does NOT need a live tmux daemon.

The live tmux integration (test_tmux.py) must be run on a real machine where a
tmux server persists; this sandbox reaps detached daemons.
"""

import re

from tmuxctl import _slice, _partial
from render import render_png, _parse_cells

# --- marker slicing -------------------------------------------------------

# A realistic capture: prompt + typed command line, then real output, then
# the marker echo output, then a fresh prompt.
CAP = "\n".join([
    "user@host:~$ echo __BEG_abc__; echo hello; echo __END_abc__:$?",
    "__BEG_abc__",
    "hello",
    "__END_abc__:0",
    "user@host:~$ ",
])

end_re = re.compile(r"^__END_abc__:(\d+)$")
res = _slice(CAP, "__BEG_abc__", end_re)
print("1 slice:", res)
assert res == ("hello", 0), res

# multiline output
CAP2 = "\n".join([
    "$ echo __BEG_x__; printf 'a\\nb\\nc\\n'; echo __END_x__:$?",
    "__BEG_x__", "a", "b", "c", "__END_x__:0", "$ ",
])
res = _slice(CAP2, "__BEG_x__", re.compile(r"^__END_x__:(\d+)$"))
print("2 multiline:", res)
assert res == ("a\nb\nc", 0), res

# non-zero exit
CAP3 = "\n".join([
    "$ ...", "__BEG_e__", "ls: no such file", "__END_e__:2", "$ ",
])
res = _slice(CAP3, "__BEG_e__", re.compile(r"^__END_e__:(\d+)$"))
print("3 exit:", res)
assert res == ("ls: no such file", 2), res

# end marker not yet present -> None (still running)
CAP4 = "\n".join(["$ ...", "__BEG_r__", "partial output so far"])
res = _slice(CAP4, "__BEG_r__", re.compile(r"^__END_r__:(\d+)$"))
print("4 running:", res)
assert res is None

# partial extraction for timeout
part = _partial(CAP4, "__BEG_r__")
print("5 partial:", repr(part))
assert part == "partial output so far"

# the typed command line must NOT be mistaken for the begin marker
# (it contains __BEG_abc__ but is not exactly equal to it)
res = _slice(CAP, "__BEG_abc__", end_re)
assert res[0] == "hello", "typed line leaked into output"
print("6 typed-line isolation: ok")

# --- render ---------------------------------------------------------------

colored = "\x1b[31mred\x1b[0m normal \x1b[1;32mboldgreen\x1b[0m\n\x1b[44mbluebg\x1b[0m"
rows = _parse_cells(colored)
print("7 parsed rows:", len(rows), "first row chars:", len(rows[0]))
assert len(rows) == 2
png = render_png(colored)
open("/tmp/claude_render_test.png", "wb").write(png)
print("8 render png bytes:", len(png), "-> /tmp/claude_render_test.png")
assert png[:8] == b"\x89PNG\r\n\x1a\n"

print("ALL LOGIC TESTS PASS")
