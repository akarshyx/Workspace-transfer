---
name: Telegram Stars recipients
description: The payment-recipient distinction between bot invoices and personal-account Telegram Gifts.
---

Telegram Bot API Stars invoices are paid to the bot's balance and cannot be redirected to a human account. To receive value on a personal Telegram account, use a Telegram Gift flow addressed to that account; keep the two routes labeled separately.

**Why:** The casino needs both in-game top-ups and a way for players to send Stars value to the owner's personal account. Calling a bot invoice a personal payment would mislead players about who receives it.

**How to apply:** When changing Stars payments, preserve the bot-invoice route for casino balance credits and the separate Gift route for a personal account. Never describe an XTR invoice as being paid directly to the owner.
