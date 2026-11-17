"""
desk.py
One Telegram group, several voices: lane routing for the PSX desk.

Why
---
The signal engine, the event gate and the risk layer all produce different kinds of
message. Dumping them into one chat buries the risk update under twelve BUY cards.
A "desk" is just a group with topics, where each lane posts its own voice:

    desk     🎛  the daily header / scan summary
    signals  🛰  the engine's BUY NOW / BUY ZONE cards
    events   🗓  announcement vetoes, each citing the event that caused them
    risk     🛡  lifecycle and exit updates (stops, TP1/TP2, time stops, trailing exits)

Two ways to run it, both supported:

  * one bot, four topics   -> set PSX_TOPIC_DESK / _SIGNALS / _EVENTS / _RISK
  * several bots           -> also set PSX_BOT_TOKEN_SIGNALS etc. (each BotFather
                              identity posts into the group, so the lanes really are
                              different bots rather than different headings)

Everything degrades to today's behaviour: with no topics and no per-lane tokens, every
lane falls back to TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID and the desk is one feed.
"""

import os

# Lane order matters only for previews/tests.
ROLES = ("desk", "signals", "events", "risk")

ROLE_BADGES = {
    "desk": "🎛 <b>DESK</b>",
    "signals": "🛰 <b>ENGINE</b>",
    "events": "🗓 <b>EVENT DESK</b>",
    "risk": "🛡 <b>RISK</b>",
}

# Ledger/lifecycle statuses that belong on the risk lane.
RISK_UPDATES = {
    "STOP_HIT", "TP1_HIT", "TP2_HIT", "TP1_BE", "TRAIL_STOP", "TIME_STOP",
    "HOLDING_CAP", "INVALIDATED", "PARTIAL_CLOSED", "PARTIAL_SKIPPED",
}
# Gate messages belong on the events lane.
EVENT_UPDATES = {"EVENT_BLOCK", "EVENT_WATCH"}


def _as_int(value):
    """Telegram thread ids are integers; anything else means 'no topic'."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def resolve_channels(environ=None, chat_id=None, token=None) -> dict:
    """
    Per-lane delivery targets.

    PSX_BOT_TOKEN_<LANE>   optional: a different bot identity for that lane
    PSX_TOPIC_<LANE>       optional: forum topic id for that lane
    Fallbacks are the supplied token / chat, then TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.
    """
    env = environ if environ is not None else os.environ
    raw_chat = str(chat_id or env.get("TELEGRAM_CHAT_ID") or "").strip()
    # A laned message needs exactly one chat. PSX_DESK_CHAT_ID is the explicit
    # target (the supergroup); otherwise the first entry of TELEGRAM_CHAT_ID is
    # used, so a comma-separated broadcast list still routes deterministically.
    desktop_chat = str(env.get("PSX_DESK_CHAT_ID") or "").strip() or (
        raw_chat.split(",")[0].strip() if raw_chat else ""
    )
    chat = desktop_chat
    main_token = str(token or env.get("TELEGRAM_BOT_TOKEN") or "").strip()

    channels = {}
    for role in ROLES:
        lane_token = str(env.get(f"PSX_BOT_TOKEN_{role.upper()}") or "").strip()
        token_value = lane_token or main_token
        channels[role] = {
            "token": token_value,
            "token_source": "own" if lane_token else ("main" if main_token else "missing"),
            "chat_id": chat,
            "thread_id": _as_int(env.get(f"PSX_TOPIC_{role.upper()}")),
            "enabled": bool(token_value and chat),
        }
    return channels


def role_for_update(update_type: str) -> str:
    """Routes a lifecycle update to its lane (risk unless it is an event veto)."""
    if update_type in EVENT_UPDATES:
        return "events"
    if update_type in RISK_UPDATES:
        return "risk"
    return "desk"


def envelope(role: str, text: str, symbol: str = None, headline: str = None) -> str:
    """Prefixes a message with its lane badge, so a shared group stays readable."""
    badge = ROLE_BADGES.get(role, ROLE_BADGES["desk"])
    header = badge
    if symbol:
        header += f" · <b>${str(symbol).upper()}</b>"
    if headline:
        header += f" — {headline}"
    return f"{header}\n{text}"


def post(role: str, text: str, send, environ=None, chat_id=None, dry_run: bool = False,
         symbol: str = None, headline: str = None, token: str = None) -> bool:
    """
    Sends one lane message through an injected `send(token, chat_id, text, thread_id)`.

    `send` is injected (not imported) so this module stays testable and free of a
    circular import with telegram_notifier. Returns False when the lane is not
    configured or the send fails -- callers treat delivery as best-effort.
    """
    if not text or dry_run:
        return False
    channels = resolve_channels(environ, chat_id, token)
    channel = channels.get(role) or channels["desk"]
    if not channel["enabled"]:
        return False
    try:
        return bool(send(channel["token"], channel["chat_id"],
                         envelope(role, text, symbol, headline), channel["thread_id"]))
    except TypeError:
        # A sender that predates thread support must not break the desk.
        return bool(send(channel["token"], channel["chat_id"],
                         envelope(role, text, symbol, headline)))
    except Exception as exc:  # delivery is best-effort by design
        print(f"✗ desk delivery failed ({role}): {exc}")
        return False


def describe(environ=None, chat_id=None, token=None) -> str:
    """Human-readable lane configuration, used by the CLI diagnostics."""
    channels = resolve_channels(environ, chat_id, token)
    lines = []
    for role in ROLES:
        channel = channels[role]
        topic = channel["thread_id"] if channel["thread_id"] is not None else "general"
        lines.append(
            f"{role:<8} {'on ' if channel['enabled'] else 'off'} "
            f"topic={topic} token={channel['token_source']}"
        )
    return "\n".join(lines)


def extract_desk_ids(payload) -> dict:
    """
    Pulls chat / topic ids out of a getUpdates payload.

    Only messages the bot can actually see appear here, which is why privacy mode
    matters: with it on, a plain message in a topic is invisible -- send an addressed
    command such as `/start@yourbot` inside each topic instead.
    """
    chats = {}
    for update in (payload or {}).get("result", []) or []:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        key = str(chat_id)
        entry = chats.setdefault(key, {
            "chat_id": key,
            "title": chat.get("title") or chat.get("username") or chat.get("first_name") or "",
            "type": chat.get("type"),
            "is_forum": bool(chat.get("is_forum")),
            "topics": {},
        })
        thread_id = message.get("message_thread_id")
        if thread_id is not None:
            created = message.get("forum_topic_created") or {}
            label = created.get("name") or (message.get("text") or "").strip()[:60]
            entry["topics"].setdefault(int(thread_id), label)
    return chats


# Lane tokens, most specific first: "Event desk" must map to events, not desk.
ROLE_TOKENS = (
    ("events", ("event",)),
    ("signals", ("signal",)),
    ("risk", ("risk",)),
    ("desk", ("desk",)),
)


def suggest_env_block(chat: dict) -> str:
    """
    Maps topic labels onto lane env vars when the names are recognisable.

    'Signals', 'Event desk' and 'Risk' all contain a lane token, so the common naming
    works automatically; anything unrecognised is printed for a manual assignment.
    """
    lines = [f"TELEGRAM_CHAT_ID={chat['chat_id']}"]
    topics = chat.get("topics", {}) or {}
    mapped = {}
    used = set()
    for role, tokens in ROLE_TOKENS:
        for thread_id, label in sorted(topics.items()):
            if thread_id in used:
                continue
            lowered = str(label or "").lower()
            if any(token in lowered for token in tokens):
                mapped[role] = thread_id
                used.add(thread_id)
                break
    for role in ROLES:
        if role in mapped:
            lines.append(f"PSX_TOPIC_{role.upper()}={mapped[role]}")
    unmapped = {thread: label for thread, label in topics.items() if thread not in used}
    if unmapped:
        lines.append("# unrecognised topics — assign manually:")
        for thread_id, label in sorted(unmapped.items()):
            lines.append(f"#   PSX_TOPIC_???={thread_id}   ({label or 'no label'})")
    return "\n".join(lines)


def format_desk_ids(chats: dict) -> str:
    """Readable report for `--desk-ids`."""
    if not chats:
        return (
            "No updates were visible to the bot.\n"
            "If the group exists: add the bot as an admin, then send an addressed command\n"
            "inside each topic — e.g. /start@your_bot — and run this again.\n"
            "(With privacy mode ON, plain messages never reach the bot.)"
        )
    blocks = []
    for chat in chats.values():
        header = f"chat {chat['chat_id']} — {chat['title']} ({chat['type']}"
        header += ", forum" if chat["is_forum"] else ""
        header += ")"
        lines = [header]
        if chat["topics"]:
            for thread_id, label in sorted(chat["topics"].items()):
                lines.append(f"  topic {thread_id}: {label or '(no label)'}")
        else:
            lines.append("  no topics seen yet — post an addressed command in each topic")
        if chat["is_forum"] and chat["topics"]:
            lines += ["", suggest_env_block(chat)]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
