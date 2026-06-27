#!/usr/bin/env python3
"""Telegram bot that drives tmux-backed shell sessions.

Each user can have multiple named sessions, each backed by a real tmux session,
so state survives bot restarts and can be attached from a terminal with
`tmux attach -t tgnb_<user>_<name>`.

Two modes per user:
  - normal (default): a message is a shell command; reply is its output.
  - raw (/a): a message is literal keystrokes; reply is the current screen.
    Used to drive TUIs like vim.
"""

import asyncio
import html
import io
import json
import logging
import os
import re

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import tmuxctl
from render import render_png

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("terminal-bot")

# --- Config ---------------------------------------------------------------


CONFIG_DIR = os.path.expanduser("~/.config/tgterm")


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from .env; don't override already-set env vars.

    Looks in ~/.config/tgterm/.env first (for global `uv tool install`), then
    ./.env in the working dir (for running from a checkout).
    """
    for path in (os.path.join(CONFIG_DIR, ".env"), ".env"):
        try:
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())
        except OSError:
            continue


_load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ALLOWED_USER_IDS = {
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x
}
DEFAULT_TIMEOUT = int(os.environ.get("CMD_TIMEOUT", "30"))
VIM_LEADER = os.environ.get("VIM_LEADER", "Space")  # tmux key name; Space = " "
STATE_FILE = os.path.join(CONFIG_DIR, "bot_state.json")
TG_LIMIT = 4096

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[=>]|\r")
NAME_RE = re.compile(r"[^A-Za-z0-9_-]")


# --- Per-user state -------------------------------------------------------


class UserState:
    def __init__(self, uid: int) -> None:
        self.uid = uid
        self.active: str | None = None
        self.mode = "normal"  # or "raw"
        self.lock = asyncio.Lock()

    @property
    def prefix(self) -> str:
        return f"tgnb_{self.uid}_"

    def full(self, name: str) -> str:
        return self.prefix + name

    def sessions(self) -> list[str]:
        return tmuxctl.list_sessions(self.prefix)

    def ensure_active(self) -> str:
        existing = self.sessions()
        if self.active not in existing:
            self.active = existing[0] if existing else None
        if self.active is None:
            tmuxctl.new_session(self.full("main"))
            self.active = "main"
        elif not tmuxctl.has_session(self.full(self.active)):
            tmuxctl.new_session(self.full(self.active))
        return self.active

    def active_full(self) -> str:
        return self.full(self.ensure_active())


_users: dict[int, UserState] = {}


def get_user(uid: int) -> UserState:
    state = _users.get(uid)
    if state is None:
        state = UserState(uid)
        _users[uid] = state
    return state


def save_state() -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        data = {str(u.uid): {"active": u.active, "mode": u.mode} for u in _users.values()}
        with open(STATE_FILE, "w") as fh:
            json.dump(data, fh)
    except OSError as exc:
        log.warning("Could not save state: %s", exc)


def load_state() -> None:
    try:
        with open(STATE_FILE) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return
    for uid_s, info in data.items():
        st = get_user(int(uid_s))
        st.active = info.get("active")
        st.mode = info.get("mode", "normal")


# --- Helpers --------------------------------------------------------------


def authorised(uid: int | None) -> bool:
    return uid is not None and uid in ALLOWED_USER_IDS


def sanitize(name: str) -> str:
    name = NAME_RE.sub("_", name.strip())[:32]
    return name or "main"


def clean(text: str) -> str:
    return ANSI_RE.sub("", text).rstrip()


def as_pre(text: str, header: str = "") -> str:
    body = clean(text) or "(no output)"
    budget = TG_LIMIT - 32 - len(header)
    if len(body) > budget:
        body = "...(truncated)\n" + body[-(budget - 20):]
    head = html.escape(header)
    return f"{head}<pre>{html.escape(body)}</pre>"


async def reply(update: Update, text: str) -> None:
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


def uid_of(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None


async def guard(update: Update) -> int | None:
    uid = uid_of(update)
    if not authorised(uid):
        log.warning("Unauthorised access from %s", uid)
        await update.effective_message.reply_text(
            f"⛔ Not authorised. Your user id is: {uid}"
        )
        return None
    return uid


# --- Normal-mode handlers -------------------------------------------------


HELP = (
    "<b>tmux terminal bot</b>\n\n"
    "<b>Normal mode</b> (default):\n"
    "• send text → run as shell command\n"
    "/new [name] – new session + switch\n"
    "/ls – list sessions\n"
    "/sw &lt;name&gt; – switch session\n"
    "/kill [name] – kill session (default: active)\n"
    "/reset – restart active session\n"
    "/c – send Ctrl-C to active\n"
    "/a [name] – attach (raw mode) for TUIs\n"
    "/screen – screenshot active pane\n\n"
    "<b>Raw mode</b> (after /a):\n"
    "• send text → literal keystrokes\n"
    "/cmd &lt;x&gt; – send :x + Enter (vim ex)\n"
    "/ldr &lt;x&gt; – send &lt;leader&gt;x\n"
    "/esc /enter /tab /space – special keys\n"
    "/d – detach (back to normal)"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if await guard(update) is None:
        return
    await reply(update, HELP)


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = await guard(update)
    if uid is None:
        return
    st = get_user(uid)
    name = sanitize(ctx.args[0]) if ctx.args else None
    if name is None:
        existing = set(st.sessions())
        i = 1
        while f"s{i}" in existing:
            i += 1
        name = f"s{i}"
    if not tmuxctl.has_session(st.full(name)):
        tmuxctl.new_session(st.full(name))
    st.active = name
    st.mode = "normal"
    save_state()
    await reply(update, f"✅ session <b>{html.escape(name)}</b> created and active.")


async def cmd_ls(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = await guard(update)
    if uid is None:
        return
    st = get_user(uid)
    st.ensure_active()
    names = st.sessions()
    lines = [
        ("➤ " if n == st.active else "  ") + n + (f"  [{st.mode}]" if n == st.active else "")
        for n in names
    ]
    await reply(update, "Sessions:\n" + ("\n".join(lines) or "(none)"))


async def cmd_sw(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = await guard(update)
    if uid is None:
        return
    st = get_user(uid)
    if not ctx.args:
        await reply(update, "Usage: /sw &lt;name&gt;")
        return
    name = sanitize(ctx.args[0])
    if name not in st.sessions():
        await reply(update, f"No such session: {html.escape(name)}")
        return
    st.active = name
    save_state()
    await reply(update, f"➤ switched to <b>{html.escape(name)}</b>")


async def cmd_kill(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = await guard(update)
    if uid is None:
        return
    st = get_user(uid)
    name = sanitize(ctx.args[0]) if ctx.args else st.ensure_active()
    if name not in st.sessions():
        await reply(update, f"No such session: {html.escape(name)}")
        return
    tmuxctl.kill_session(st.full(name))
    if st.active == name:
        st.active = None
    save_state()
    await reply(update, f"🗑️ killed <b>{html.escape(name)}</b>")


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = await guard(update)
    if uid is None:
        return
    st = get_user(uid)
    name = st.ensure_active()
    tmuxctl.kill_session(st.full(name))
    tmuxctl.new_session(st.full(name))
    st.mode = "normal"
    save_state()
    await reply(update, f"♻️ session <b>{html.escape(name)}</b> reset.")


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = await guard(update)
    if uid is None:
        return
    st = get_user(uid)
    tmuxctl.send_key(st.active_full(), "C-c")
    await asyncio.sleep(0.3)
    cap = await asyncio.to_thread(tmuxctl.capture, st.active_full())
    await reply(update, as_pre(cap, "^C\n"))


async def cmd_attach(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = await guard(update)
    if uid is None:
        return
    st = get_user(uid)
    if ctx.args:
        name = sanitize(ctx.args[0])
        if name not in st.sessions() and not tmuxctl.has_session(st.full(name)):
            tmuxctl.new_session(st.full(name))
        st.active = name
    st.ensure_active()
    st.mode = "raw"
    save_state()
    cap = await asyncio.to_thread(tmuxctl.capture, st.active_full())
    await reply(update, as_pre(cap, f"📎 raw mode on <b>{html.escape(st.active)}</b>. /d to detach.\n"))


async def cmd_detach(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = await guard(update)
    if uid is None:
        return
    st = get_user(uid)
    st.mode = "normal"
    save_state()
    await reply(update, "↩️ back to normal mode.")


async def cmd_screen(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = await guard(update)
    if uid is None:
        return
    st = get_user(uid)
    colored = await asyncio.to_thread(tmuxctl.capture, st.active_full(), False, True)
    png = await asyncio.to_thread(render_png, colored)
    await update.effective_message.reply_photo(
        photo=io.BytesIO(png), caption=f"📸 {st.active}"
    )


# --- Raw-mode key handlers ------------------------------------------------


async def _raw_then_screen(update: Update, st: UserState) -> None:
    await asyncio.sleep(0.25)
    cap = await asyncio.to_thread(tmuxctl.capture, st.active_full())
    await reply(update, as_pre(cap))


async def cmd_vimcmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = await guard(update)
    if uid is None:
        return
    st = get_user(uid)
    arg = " ".join(ctx.args)
    full = st.active_full()
    tmuxctl.send_literal(full, ":" + arg)
    tmuxctl.send_key(full, "Enter")
    await _raw_then_screen(update, st)


async def cmd_leader(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = await guard(update)
    if uid is None:
        return
    st = get_user(uid)
    arg = " ".join(ctx.args)
    full = st.active_full()
    tmuxctl.send_key(full, VIM_LEADER)
    if arg:
        tmuxctl.send_literal(full, arg)
    await _raw_then_screen(update, st)


def _special_handler(key: str):
    async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        uid = await guard(update)
        if uid is None:
            return
        st = get_user(uid)
        tmuxctl.send_key(st.active_full(), key)
        await _raw_then_screen(update, st)
    return handler


# --- Plain-text dispatch --------------------------------------------------


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = await guard(update)
    if uid is None:
        return
    text = update.effective_message.text
    if not text:
        return
    st = get_user(uid)

    if st.mode == "raw":
        full = st.active_full()
        tmuxctl.send_literal(full, text)
        await _raw_then_screen(update, st)
        return

    # Normal mode: run as a shell command.
    if st.lock.locked():
        await reply(update, "⏳ a command is still running. /c to cancel.")
        return
    async with st.lock:
        await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        full = st.active_full()
        out, code, timed_out = await asyncio.to_thread(
            tmuxctl.run_command, full, text, DEFAULT_TIMEOUT
        )
    header = ""
    if timed_out:
        header = "⏳ still running — /c to cancel\n"
    elif code not in (0, None):
        header = f"⚠️ exit {code}\n"
    await reply(update, as_pre(out, header))


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN env var is required")
    if not ALLOWED_USER_IDS:
        raise SystemExit("ALLOWED_USER_IDS env var is required (comma-separated)")

    tmuxctl.server_init()
    load_state()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("ls", cmd_ls))
    app.add_handler(CommandHandler("sw", cmd_sw))
    app.add_handler(CommandHandler("kill", cmd_kill))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("c", cmd_cancel))
    app.add_handler(CommandHandler("a", cmd_attach))
    app.add_handler(CommandHandler("d", cmd_detach))
    app.add_handler(CommandHandler("screen", cmd_screen))
    app.add_handler(CommandHandler("cmd", cmd_vimcmd))
    app.add_handler(CommandHandler("ldr", cmd_leader))
    app.add_handler(CommandHandler("esc", _special_handler("Escape")))
    app.add_handler(CommandHandler("enter", _special_handler("Enter")))
    app.add_handler(CommandHandler("tab", _special_handler("Tab")))
    app.add_handler(CommandHandler("space", _special_handler("Space")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot starting. Authorised users: %s", ALLOWED_USER_IDS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
