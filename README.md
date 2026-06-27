# terminal_bot

A Telegram bot that drives **tmux-backed shell sessions**. Each user can have
several named sessions; each is a real tmux session, so state survives bot
restarts and you can `tmux attach -t tgnb_<user>_<name>` from a real terminal.

Built with [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot),
[tmux](https://github.com/tmux/tmux), and [Pillow](https://python-pillow.org/)
(for `/screen`).

## Modes

**Normal mode (default)** — a message is a shell command; the reply is its
output (extracted via unique markers, so you get exactly that command's output
and its exit code).

| command | action |
|---------|--------|
| *(text)* | run as shell command in active session |
| `/new [name]` | new session + switch (auto-named if omitted) |
| `/ls` | list sessions, mark active |
| `/sw <name>` | switch active session |
| `/kill [name]` | kill session (default: active) |
| `/reset` | restart the active session |
| `/c` | send Ctrl-C to the active session |
| `/a [name]` | attach → raw mode (for TUIs) |
| `/screen` | screenshot the active pane (rendered PNG) |

**Raw mode (after `/a`)** — a message is literal keystrokes; the reply is the
current screen as text. For driving TUIs like vim.

| command | sends |
|---------|-------|
| *(text)* | literal keystrokes |
| `/cmd wq` | `:wq` + Enter (vim ex command) |
| `/ldr ff` | `<leader>` + `ff` (leader = `VIM_LEADER`, default Space) |
| `/esc` `/enter` `/tab` `/space` | special keys |
| `/screen` | screenshot |
| `/d` | detach → normal mode |

## TUIs and screenshots

`tmux capture-pane` returns the **rendered screen as text**, so vim/htop are
readable in Telegram's monospace without any image. `/screen` is only for when
text isn't enough (colours/box-drawing matter) — it's never automatic.

Running `claude`: prefer non-interactive `claude -p "..."` in normal mode. The
interactive TUI works via raw mode if you want it.

## Setup

Requires `tmux` installed (`pacman -S tmux`).

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env          # set BOT_TOKEN and ALLOWED_USER_IDS
./run.sh
```

Find your numeric id: message the bot; if not whitelisted it replies with your
id. Put it in `ALLOWED_USER_IDS` and restart.

> **Note:** run this on a normal machine, not inside a sandbox that reaps
> detached processes — tmux needs a persistent server.

## Security

Full shell access as the user running the bot.

- Only `ALLOWED_USER_IDS` may use it; everyone else is rejected and logged.
- Keep `BOT_TOKEN` secret; never commit `.env`.
- Don't run as root; use a normal, ideally dedicated, account.

## Tests

```bash
.venv/bin/python test_logic.py   # pure logic: marker slicing + render (no tmux)
.venv/bin/python test_tmux.py    # live tmux integration (needs a real tmux server)
```
