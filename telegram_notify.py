"""Minimal Telegram notification helper."""

import requests
import config


def send_message(text: str) -> None:
    """Send a message to the configured Telegram chat. Falls back to printing
    to the console if the bot token / chat id haven't been configured yet."""
    if "YOUR_TELEGRAM" in config.TELEGRAM_BOT_TOKEN or "YOUR_TELEGRAM" in config.TELEGRAM_CHAT_ID:
        print(f"[Telegram disabled — would send] {text}")
        return

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"Telegram send failed: {e}")
