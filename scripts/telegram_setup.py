"""One-time Telegram wiring helper for VC Scout.

Creating the bot is the one step that cannot be automated — Telegram only
allows it through @BotFather in the Telegram app. Everything after that, this
script handles: it finds your chat id, sends a test message, and (optionally)
writes both values into .env.

Steps:
  1. Open Telegram, message @BotFather, send /newbot, follow the prompts,
     and copy the bot token it gives you (looks like 123456:ABC-DEF...).
  2. Open a chat with your new bot and send it any message (e.g. "hi") —
     the bot can only discover your chat id from a message you sent it.
  3. Run this script:

       python scripts/telegram_setup.py <BOT_TOKEN>
       python scripts/telegram_setup.py <BOT_TOKEN> --write-env     # also persist to .env
       python scripts/telegram_setup.py <BOT_TOKEN> --chat-id <ID>  # skip auto-detection

For GitHub Actions, also set the two values as repo secrets — the script
prints the exact `gh secret set` commands at the end.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{method}"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def resolve_chat_id(token: str) -> str | None:
    """Return the most recent chat id that has messaged the bot, or None."""
    resp = requests.get(API.format(token=token, method="getUpdates"), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise SystemExit(f"Telegram rejected the token: {data.get('description', data)}")
    chat_ids: list[str] = []
    for update in data.get("result", []):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if "id" in chat:
            chat_ids.append(str(chat["id"]))
    return chat_ids[-1] if chat_ids else None


def send_test(token: str, chat_id: str) -> bool:
    """Send a confirmation message to the chat. Returns True on success."""
    resp = requests.post(
        API.format(token=token, method="sendMessage"),
        json={"chat_id": chat_id, "text": "VC Scout is wired up. Alerts will arrive here."},
        timeout=15,
    )
    return resp.ok and resp.json().get("ok", False)


def write_env(token: str, chat_id: str) -> Path:
    """Upsert TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID into .env, preserving other keys."""
    updates = {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id}
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    return ENV_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description="Wire up Telegram alerts for VC Scout.")
    parser.add_argument("token", help="Bot token from @BotFather")
    parser.add_argument("--chat-id", help="Chat id, if you already know it (skips auto-detection)")
    parser.add_argument("--write-env", action="store_true", help="Persist both values into .env")
    args = parser.parse_args()

    chat_id = args.chat_id or resolve_chat_id(args.token)
    if not chat_id:
        print(
            "No chat id found. Open Telegram, send your bot any message, then re-run.\n"
            "(getUpdates only sees messages sent after the bot was created, and within ~24h.)"
        )
        return 1
    print(f"Chat id: {chat_id}")

    if not send_test(args.token, chat_id):
        print("Could not send the test message — double-check the bot token.")
        return 1
    print("Test message sent — check Telegram to confirm it arrived.")

    if args.write_env:
        print(f"Wrote TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID to {write_env(args.token, chat_id)}")
    else:
        print("\nAdd these to your .env (or re-run with --write-env):")
        print(f"  TELEGRAM_BOT_TOKEN={args.token}")
        print(f"  TELEGRAM_CHAT_ID={chat_id}")

    print("\nFor GitHub Actions, set them as repo secrets:")
    print(f"  gh secret set TELEGRAM_BOT_TOKEN --repo <owner>/<repo> --body {args.token}")
    print(f"  gh secret set TELEGRAM_CHAT_ID --repo <owner>/<repo> --body {chat_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
