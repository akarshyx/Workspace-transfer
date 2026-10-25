# Telegram gift purchase commands

The purchase tools are restricted to the bot owner and work only in the bot's private chat.

## Supported purchase paths

- `/gifts` reads Telegram's live bot gift catalog and the bot's current Stars balance.
- `/giftbuy <gift_id> <recipient_user_id>` prepares a standard catalog gift bought with the **bot's** Stars.
- `/giftbuy https://t.me/nft/<slug> <me|@username>` verifies a collectible resale listing and prepares checkout with a separately authorized **user account's** Stars.

Both purchase paths show the item, recipient, live price, checkout total, fee/adjustment information available from Telegram, and payment source. Funds are not spent until the owner presses **Confirm purchase**. The approval expires after ten minutes. Price and balance are checked again before the purchase request is sent.

Bot Stars can pay for regular gifts sent through Bot API `sendGift`; they cannot pay for a resale collectible bought by a separate user account. Collectible resale inspection and purchase are user-account-only MTProto operations. This implementation supports Stars listings only; a TON-only listing is rejected before approval. It does not place offers, join auctions, or buy automatically.

Official references:

- [Telegram Bot API: getAvailableGifts and sendGift](https://core.telegram.org/bots/api)
- [Telegram Gifts and collectible resale flow](https://core.telegram.org/api/gifts)
- [payments.getUniqueStarGift](https://core.telegram.org/method/payments.getUniqueStarGift)
- [payments.getResaleStarGifts](https://core.telegram.org/method/payments.getResaleStarGifts)
- [inputInvoiceStarGiftResale](https://core.telegram.org/constructor/inputInvoiceStarGiftResale)
- [Telegram Payments API](https://core.telegram.org/api/payments)

## Optional collectible-account setup

The resale flow requires a Telegram API application ID and hash, plus a separately authorized Telegram user session. Keep `TELEGRAM_API_HASH` in Replit Secrets. `TELEGRAM_API_ID` may also be kept there. Do not place either value in source control or send login codes through the bot.

1. Add `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` to the bot's environment.
2. Open the project shell and run `python scripts/setup_telegram_user_session.py`.
3. Enter the phone number, Telegram login code, and (if enabled) two-step verification password only at the hidden prompts.
4. The script stores the authorized session outside the project with owner-only file permissions. It does not print the session or login code.
5. Restarting the bot is required after setting up or changing the user session. The running bot does not read a new session file until it restarts.

The default session file is under `~/.local/share/rollers-casino/`. If `TELEGRAM_USER_SESSION_FILE` is set, it must point outside the project directory and to a protected location. Keep that file private; possession of it grants access to the Telegram user account.

## Safety and transaction records

Every request is recorded in `gift_purchases.sqlite3`. Confirmation is one-shot, duplicate callback deliveries cannot resend the same purchase, and uncertain network results are marked `unknown` and are never retried automatically. For an `unknown` result, check the recipient's gifts and the paying account's Stars balance before attempting another purchase.

If Telegram does not provide an exact Stars checkout total, if a listing is unavailable, if the price changes, or if the authorized account lacks enough Stars, no purchase is sent. A changed price creates a new preview and requires a new approval.

No real purchase is made during startup or testing.
