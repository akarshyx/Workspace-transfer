"""
styled_buttons.py — Bot API 9.4+ button styling for python-telegram-bot.

python-telegram-bot (PTB) v20/v21 does NOT yet expose the Bot API 9.4 fields
`style` and `icon_custom_emoji_id` on InlineKeyboardButton.

This module works around that by subclassing InlineKeyboardButton and
overriding to_dict() so the extra fields are injected into the JSON payload
that PTB sends to the Telegram API — no direct HTTP requests needed.

Usage:
    from styled_buttons import primary_btn, success_btn, danger_btn

    kb = InlineKeyboardMarkup([
        [primary_btn("🎮 Play",    callback_data="play")],
        [success_btn("💰 Deposit", callback_data="deposit")],
        [danger_btn("❌ Cancel",   callback_data="cancel")],
    ])
"""

from __future__ import annotations
from typing import Optional
from telegram import InlineKeyboardButton

PRIMARY = "primary"   # blue  — Play, VIP, Support, navigation
SUCCESS = "success"   # green — Deposit, Release, Confirm
DANGER  = "danger"    # red   — Cancel, Dispute, Delete


class StyledInlineKeyboardButton(InlineKeyboardButton):
    """
    Drop-in replacement for InlineKeyboardButton that adds Bot API 9.4+ fields:
      • style               — "primary" | "success" | "danger"
      • icon_custom_emoji_id — custom emoji shown on the button

    PTB does not yet natively support these fields, so we override to_dict()
    to inject them into the serialised payload that gets sent to Telegram.
    """

    __slots__ = ("_btn_style", "_icon_custom_emoji_id")

    def __init__(
        self,
        text: str,
        *args,
        style: Optional[str] = None,
        icon_custom_emoji_id: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(text, *args, **kwargs)
        self._btn_style = style
        self._icon_custom_emoji_id = icon_custom_emoji_id

    def to_dict(self, recursive: bool = True) -> dict:
        data = super().to_dict(recursive=recursive)
        if self._btn_style is not None:
            data["style"] = self._btn_style
        if self._icon_custom_emoji_id is not None:
            data["icon_custom_emoji_id"] = self._icon_custom_emoji_id
        return data


# ── Convenience helpers ───────────────────────────────────────────────────────

def primary_btn(
    text: str,
    *,
    callback_data: Optional[str] = None,
    url: Optional[str] = None,
    icon_custom_emoji_id: Optional[str] = None,
    **kwargs,
) -> StyledInlineKeyboardButton:
    """Blue (primary) button — Play, VIP, Support, navigation."""
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    return StyledInlineKeyboardButton(
        text, style=PRIMARY, icon_custom_emoji_id=icon_custom_emoji_id, **kwargs
    )


def success_btn(
    text: str,
    *,
    callback_data: Optional[str] = None,
    url: Optional[str] = None,
    icon_custom_emoji_id: Optional[str] = None,
    **kwargs,
) -> StyledInlineKeyboardButton:
    """Green (success) button — Deposit, Release, Confirm."""
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    return StyledInlineKeyboardButton(
        text, style=SUCCESS, icon_custom_emoji_id=icon_custom_emoji_id, **kwargs
    )


def danger_btn(
    text: str,
    *,
    callback_data: Optional[str] = None,
    url: Optional[str] = None,
    icon_custom_emoji_id: Optional[str] = None,
    **kwargs,
) -> StyledInlineKeyboardButton:
    """Red (danger) button — Cancel, Dispute, Delete."""
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    return StyledInlineKeyboardButton(
        text, style=DANGER, icon_custom_emoji_id=icon_custom_emoji_id, **kwargs
    )
