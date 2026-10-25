"""Owner-controlled Telegram gift purchases with explicit, one-shot approval."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import unquote, urlsplit

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PROJECT_ROOT / "gift_purchases.sqlite3"
DEFAULT_SESSION_PATH = Path.home() / ".local/share/rollers-casino/gift-purchase.session"
PENDING_TTL_SECONDS = 10 * 60
CALLBACK_PATTERN = r"^giftpurchase:(confirm|cancel):[0-9a-f]{16}$"
SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
USERNAME_PATTERN = re.compile(r"^@?[A-Za-z0-9_]{5,32}$")


class GiftPurchaseError(Exception):
    """A safe, user-facing reason a gift cannot currently be purchased."""


class DuplicatePurchaseError(GiftPurchaseError):
    pass


def parse_collectible_link(value: str) -> str:
    """Accept only Telegram's official collectible gift link form."""
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    try:
        valid_port = parsed.port in {None, 443}
    except ValueError:
        valid_port = False
    if parsed.scheme != "https" or host not in {"t.me", "telegram.me"} or not valid_port:
        raise GiftPurchaseError("Use an official Telegram collectible link, such as https://t.me/nft/<slug>.")

    segments = [part for part in parsed.path.split("/") if part]
    if len(segments) != 2 or segments[0].lower() != "nft":
        raise GiftPurchaseError("This link is not a supported Telegram collectible gift link.")

    slug = unquote(segments[1])
    if not SLUG_PATTERN.fullmatch(slug):
        raise GiftPurchaseError("The collectible link contains an invalid gift identifier.")
    return slug


def stars_decimal(value: object) -> Decimal:
    """Convert Telegram StarsAmount/StarAmount or a numeric value to Stars."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float, str)):
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:
            raise GiftPurchaseError("Telegram returned an invalid Stars amount.") from exc

    amount = getattr(value, "amount", None)
    nanos = getattr(value, "nanos", getattr(value, "nanostar_amount", 0))
    try:
        return Decimal(int(amount)) + Decimal(int(nanos or 0)) / Decimal(1_000_000_000)
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise GiftPurchaseError("Telegram did not return a usable Stars amount.") from exc


def format_stars(value: Decimal | int | float | str) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"{amount.normalize():f}"


def _market_listing_price(gift: object) -> Decimal:
    if bool(getattr(gift, "burned", False)):
        raise GiftPurchaseError("This collectible has been burned and cannot be purchased.")
    if bool(getattr(gift, "resale_ton_only", False)):
        raise GiftPurchaseError("This listing only accepts TON. This bot currently supports Stars purchases only.")
    prices = getattr(gift, "resell_amount", None) or []
    if len(prices) != 1:
        raise GiftPurchaseError(
            "Telegram did not return one unambiguous Stars listing price. No purchase was prepared."
        )
    price = stars_decimal(prices[0])
    if price <= 0:
        raise GiftPurchaseError("This collectible is not currently listed for sale.")
    return price


def _payment_form_total(form: object) -> Decimal:
    invoice = getattr(form, "invoice", None)
    if invoice is None or getattr(invoice, "currency", None) != "XTR":
        raise GiftPurchaseError("Telegram did not return a Stars checkout form for this listing.")
    prices = getattr(invoice, "prices", None) or []
    if not prices:
        raise GiftPurchaseError(
            "Telegram did not disclose an exact checkout total. No purchase button was created."
        )
    try:
        total = sum((Decimal(int(item.amount)) for item in prices), Decimal(0))
    except (AttributeError, TypeError, ValueError, InvalidOperation) as exc:
        raise GiftPurchaseError("Telegram returned an invalid checkout total.") from exc
    if total <= 0:
        raise GiftPurchaseError("Telegram returned an invalid checkout total.")
    return total


class PurchaseStore:
    """Small SQLite ledger with atomic one-shot confirmation state changes."""

    def __init__(self, path: str | Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS gift_purchases (
                    request_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    owner_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    item_key TEXT NOT NULL,
                    item_title TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    recipient_name TEXT NOT NULL,
                    approved_listing_price TEXT NOT NULL,
                    approved_total TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    fee_amount TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    result_note TEXT NOT NULL DEFAULT ''
                )
                """
            )
            db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS gift_market_pending_unique
                ON gift_purchases(source, item_key)
                WHERE source = 'mtproto' AND status IN ('pending', 'processing', 'unknown')
                """
            )
            db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS gift_bot_pending_unique
                ON gift_purchases(source, item_key, recipient)
                WHERE source = 'bot_api' AND status IN ('pending', 'processing', 'unknown')
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 15000")
        return db

    def _expire_old_pending(self, db: sqlite3.Connection, now: float) -> None:
        db.execute(
            """
            UPDATE gift_purchases
            SET status = 'expired', updated_at = ?
            WHERE status = 'pending' AND expires_at <= ?
            """,
            (now, now),
        )

    def create_pending(
        self,
        *,
        source: str,
        owner_id: int,
        chat_id: int,
        item_key: str,
        item_title: str,
        recipient: str,
        recipient_name: str,
        listing_price: Decimal,
        total: Decimal,
        currency: str,
        fee_amount: Decimal,
        details: dict,
        now: float | None = None,
    ) -> str:
        timestamp = time.time() if now is None else now
        request_id = secrets.token_hex(8)
        with self._connect() as db:
            self._expire_old_pending(db, timestamp)
            try:
                db.execute(
                    """
                    INSERT INTO gift_purchases (
                        request_id, source, status, owner_id, chat_id, item_key,
                        item_title, recipient, recipient_name, approved_listing_price,
                        approved_total, currency, fee_amount, details_json,
                        created_at, expires_at, updated_at
                    ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        source,
                        int(owner_id),
                        int(chat_id),
                        str(item_key),
                        str(item_title),
                        str(recipient),
                        str(recipient_name),
                        format_stars(listing_price),
                        format_stars(total),
                        str(currency),
                        format_stars(fee_amount),
                        json.dumps(details, separators=(",", ":")),
                        timestamp,
                        timestamp + PENDING_TTL_SECONDS,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicatePurchaseError(
                    "There is already a pending or unresolved request for this gift and recipient."
                ) from exc
        return request_id

    def get(self, request_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM gift_purchases WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return dict(row) if row else None

    def claim(self, request_id: str, *, now: float | None = None) -> tuple[str, dict | None]:
        timestamp = time.time() if now is None else now
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire_old_pending(db, timestamp)
            updated = db.execute(
                """
                UPDATE gift_purchases
                SET status = 'processing', updated_at = ?
                WHERE request_id = ? AND status = 'pending' AND expires_at > ?
                """,
                (timestamp, request_id, timestamp),
            ).rowcount
            row = db.execute(
                "SELECT * FROM gift_purchases WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if updated:
                return "claimed", dict(row)
            return (str(row["status"]), dict(row)) if row else ("missing", None)

    def finish(self, request_id: str, status: str, result_note: str = "") -> None:
        allowed = {"succeeded", "failed", "unknown", "cancelled", "price_changed"}
        if status not in allowed:
            raise ValueError("Invalid terminal gift purchase status")
        with self._connect() as db:
            db.execute(
                """
                UPDATE gift_purchases
                SET status = ?, result_note = ?, updated_at = ?
                WHERE request_id = ? AND status IN ('pending', 'processing')
                """,
                (status, result_note[:200], time.time(), request_id),
            )


class GiftPurchaseService:
    def __init__(
        self,
        owner_id: int,
        store: PurchaseStore | None = None,
        project_root: Path = PROJECT_ROOT,
    ):
        self.owner_id = int(owner_id)
        self.store = store or PurchaseStore(
            os.environ.get("GIFT_PURCHASE_DB", str(DEFAULT_DB_PATH))
        )
        self.project_root = project_root.resolve()
        self._market_client = None
        self._market_lock = asyncio.Lock()
        self._purchase_lock = asyncio.Lock()

    def _session_path(self) -> Path:
        configured = os.environ.get("TELEGRAM_USER_SESSION_FILE")
        path = Path(configured).expanduser() if configured else DEFAULT_SESSION_PATH
        resolved = path.resolve()
        try:
            resolved.relative_to(self.project_root)
        except ValueError:
            return resolved
        raise GiftPurchaseError(
            "The user-account session must be stored outside the project directory. "
            "Run the session setup script with its default location."
        )

    async def _get_market_client(self):
        api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
        api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
        if not api_id or not api_hash:
            raise GiftPurchaseError(
                "Collectible resale purchases need a separately authorized Telegram user account. "
                "The bot's Stars cannot pay for this purchase."
            )

        session_path = self._session_path()
        if not session_path.is_file():
            raise GiftPurchaseError(
                "The authorized user session is not set up. Add TELEGRAM_API_ID and "
                "TELEGRAM_API_HASH in the project's environment, then run "
                "python scripts/setup_telegram_user_session.py in the project shell."
            )

        if self._market_client is not None and self._market_client.is_connected():
            return self._market_client

        async with self._market_lock:
            if self._market_client is not None and self._market_client.is_connected():
                return self._market_client

            try:
                from telethon import TelegramClient

                client = TelegramClient(
                    str(session_path),
                    int(api_id),
                    api_hash,
                    connection_retries=1,
                    request_retries=1,
                    auto_reconnect=False,
                    timeout=15,
                )
                await client.connect()
                if not await client.is_user_authorized():
                    await client.disconnect()
                    raise GiftPurchaseError(
                        "The saved Telegram user session is not authorized. "
                        "Run the session setup script again."
                    )
            except GiftPurchaseError:
                raise
            except Exception as exc:
                logger.warning("MTProto gift session unavailable (%s)", type(exc).__name__)
                raise GiftPurchaseError(
                    "The authorized Telegram user account could not connect. No purchase was attempted."
                ) from None

            self._market_client = client
            return client

    async def shutdown(self) -> None:
        client, self._market_client = self._market_client, None
        if client is not None:
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception as exc:
                logger.warning("MTProto gift session shutdown failed (%s)", type(exc).__name__)

    async def inspect_market_item(self, link: str, recipient_spec: str) -> dict:
        slug = parse_collectible_link(link)
        recipient_spec = recipient_spec.strip()
        if recipient_spec.lower() != "me" and not USERNAME_PATTERN.fullmatch(recipient_spec):
            raise GiftPurchaseError("Use recipient `me` or a Telegram username such as `@name`.")

        client = await self._get_market_client()
        from telethon import functions, types

        recipient_entity = (
            await client.get_me()
            if recipient_spec.lower() == "me"
            else await client.get_entity(
                recipient_spec if recipient_spec.startswith("@") else f"@{recipient_spec}"
            )
        )
        if not isinstance(recipient_entity, types.User) or bool(
            getattr(recipient_entity, "bot", False)
        ):
            raise GiftPurchaseError("The recipient must be a Telegram user account, not a bot or channel.")

        peer = await client.get_input_entity(recipient_entity)
        unique = await client(
            functions.payments.GetUniqueStarGiftRequest(slug=slug)
        )
        gift = getattr(unique, "gift", None)
        if gift is None or str(getattr(gift, "slug", "")) != slug:
            raise GiftPurchaseError("Telegram could not verify this collectible gift link.")

        listing_price = _market_listing_price(gift)
        invoice = types.InputInvoiceStarGiftResale(
            slug=slug,
            to_id=peer,
        )
        form = await client(functions.payments.GetPaymentFormRequest(invoice=invoice))
        total = _payment_form_total(form)
        if total < listing_price:
            raise GiftPurchaseError(
                "Telegram's checkout total did not match the listed price. No purchase was prepared."
            )

        status = await client(
            functions.payments.GetStarsStatusRequest(peer=types.InputPeerSelf())
        )
        balance = stars_decimal(getattr(status, "balance", None))
        if balance < total:
            raise GiftPurchaseError(
                f"The authorized user account has {format_stars(balance)} Stars, "
                f"but this checkout requires {format_stars(total)} Stars."
            )

        title = str(getattr(gift, "title", "Collectible gift"))
        number = getattr(gift, "num", None)
        if number is not None:
            title = f"{title} #{number}"
        if bool(getattr(recipient_entity, "self", False)) or recipient_spec.lower() == "me":
            recipient_name = "Your authorized Telegram account"
        else:
            recipient_name = (
                f"@{recipient_entity.username}"
                if getattr(recipient_entity, "username", None)
                else f"Telegram user {recipient_entity.id}"
            )

        return {
            "source": "mtproto",
            "slug": slug,
            "item_title": title,
            "recipient": recipient_spec,
            "recipient_name": recipient_name,
            "recipient_id": str(recipient_entity.id),
            "listing_price": listing_price,
            "total": total,
            "fee_amount": total - listing_price,
            "balance": balance,
            "form_id": int(form.form_id),
            "invoice": invoice,
            "peer": peer,
        }

    async def _refresh_market_snapshot(self, row: dict) -> dict:
        details = json.loads(row["details_json"])
        snapshot = await self.inspect_market_item(
            f"https://t.me/nft/{row['item_key']}",
            details["recipient_spec"],
        )
        if snapshot["recipient_id"] != details["recipient_id"]:
            raise GiftPurchaseError(
                "The recipient username now resolves to a different account. No purchase was made."
            )
        return snapshot

    async def _send_preview(
        self,
        *,
        message,
        source: str,
        chat_id: int,
        owner_id: int,
        item_key: str,
        item_title: str,
        recipient: str,
        recipient_name: str,
        listing_price: Decimal,
        total: Decimal,
        fee_amount: Decimal,
        details: dict,
        balance: Decimal | None = None,
    ) -> None:
        request_id = self.store.create_pending(
            source=source,
            owner_id=owner_id,
            chat_id=chat_id,
            item_key=item_key,
            item_title=item_title,
            recipient=recipient,
            recipient_name=recipient_name,
            listing_price=listing_price,
            total=total,
            currency="Stars",
            fee_amount=fee_amount,
            details=details,
        )
        if fee_amount == 0:
            fee_line = "Additional fees: none listed separately by Telegram."
        elif fee_amount > 0:
            fee_line = f"Additional checkout amount: {format_stars(fee_amount)} Stars."
        else:
            fee_line = f"Checkout adjustment: {format_stars(fee_amount)} Stars."

        preview = (
            "Gift purchase review\n"
            f"Item: {item_title}\n"
            f"Recipient: {recipient_name}\n"
            f"Listed price: {format_stars(listing_price)} Stars\n"
            f"Checkout total: {format_stars(total)} Stars\n"
            f"{fee_line}\n"
            f"Payment source: {'bot' if source == 'bot_api' else 'authorized user'} Telegram Stars"
        )
        if balance is not None:
            preview += f"\nAvailable balance: {format_stars(balance)} Stars"
        preview += (
            "\n\nNo funds are spent until you press Confirm purchase. "
            "This approval expires in 10 minutes."
        )
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "Confirm purchase",
                    callback_data=f"giftpurchase:confirm:{request_id}",
                ),
                InlineKeyboardButton(
                    "Cancel",
                    callback_data=f"giftpurchase:cancel:{request_id}",
                ),
            ]]
        )
        await message.reply_text(preview, reply_markup=keyboard)

    async def gifts_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized_private(update):
            return
        try:
            catalog = await context.bot.get_available_gifts()
            balance = stars_decimal(await context.bot.get_my_star_balance())
        except Exception as exc:
            logger.warning("Bot gift catalog request failed (%s)", type(exc).__name__)
            await update.effective_message.reply_text(
                "Telegram's gift catalog or bot Stars balance is unavailable right now. No purchase was attempted."
            )
            return

        gifts = list(getattr(catalog, "gifts", []) or [])
        if not gifts:
            await update.effective_message.reply_text(
                f"No sendable gifts are listed. Bot Stars balance: {format_stars(balance)}."
            )
            return

        lines = [
            f"Bot gift catalog — available bot balance: {format_stars(balance)} Stars",
            "Use /giftbuy <gift_id> <recipient_user_id> to review a gift.",
        ]
        for gift in gifts:
            remaining = getattr(gift, "remaining_count", None)
            personal_remaining = getattr(gift, "personal_remaining_count", None)
            sold_out = remaining == 0 or personal_remaining == 0
            state = "sold out" if sold_out else "available"
            lines.append(
                f"ID {gift.id} — {int(gift.star_count)} Stars — {state}"
            )

        chunk = ""
        for line in lines:
            candidate = f"{chunk}\n{line}" if chunk else line
            if len(candidate) > 3800:
                await update.effective_message.reply_text(chunk)
                chunk = line
            else:
                chunk = candidate
        if chunk:
            await update.effective_message.reply_text(chunk)

    async def giftbuy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized_private(update):
            return
        args = list(context.args or [])
        if len(args) != 2:
            await update.effective_message.reply_text(
                "Usage:\n"
                "/giftbuy <gift_id> <recipient_user_id> — regular gift using bot Stars\n"
                "/giftbuy https://t.me/nft/<slug> <me|@username> — collectible resale using an authorized user account"
            )
            return

        item, recipient = args
        if item.lower().startswith(("https://", "http://")):
            await self._prepare_market_purchase(update, item, recipient)
        else:
            await self._prepare_bot_purchase(update, context, item, recipient)

    async def _prepare_bot_purchase(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, gift_id: str, recipient: str
    ) -> None:
        try:
            recipient_id = int(recipient)
            if recipient_id <= 0:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("For a regular bot gift, recipient must be a Telegram user ID.")
            return

        try:
            catalog = await context.bot.get_available_gifts()
            gift = next(
                (entry for entry in (getattr(catalog, "gifts", []) or []) if str(entry.id) == gift_id),
                None,
            )
            if gift is None:
                raise GiftPurchaseError("That gift ID is not in Telegram's current bot gift catalog.")
            if (
                getattr(gift, "remaining_count", None) == 0
                or getattr(gift, "personal_remaining_count", None) == 0
            ):
                raise GiftPurchaseError("That gift is currently sold out.")
            price = Decimal(int(gift.star_count))
            balance = stars_decimal(await context.bot.get_my_star_balance())
            if balance < price:
                raise GiftPurchaseError(
                    f"The bot has {format_stars(balance)} Stars, but this gift costs {format_stars(price)}."
                )

            title = f"Telegram gift ID {gift.id}"
            await self._send_preview(
                message=update.effective_message,
                source="bot_api",
                chat_id=update.effective_chat.id,
                owner_id=self.owner_id,
                item_key=str(gift.id),
                item_title=title,
                recipient=str(recipient_id),
                recipient_name=f"Telegram user {recipient_id}",
                listing_price=price,
                total=price,
                fee_amount=Decimal(0),
                details={"gift_id": str(gift.id)},
                balance=balance,
            )
        except GiftPurchaseError as exc:
            await update.effective_message.reply_text(str(exc))
        except DuplicatePurchaseError as exc:
            await update.effective_message.reply_text(str(exc))
        except Exception as exc:
            logger.warning("Bot gift preview failed (%s)", type(exc).__name__)
            await update.effective_message.reply_text(
                "Telegram could not prepare this gift preview. No purchase was attempted."
            )

    async def _prepare_market_purchase(
        self, update: Update, link: str, recipient: str
    ) -> None:
        try:
            snapshot = await self.inspect_market_item(link, recipient)
            await self._send_preview(
                message=update.effective_message,
                source="mtproto",
                chat_id=update.effective_chat.id,
                owner_id=self.owner_id,
                item_key=snapshot["slug"],
                item_title=snapshot["item_title"],
                recipient=recipient,
                recipient_name=snapshot["recipient_name"],
                listing_price=snapshot["listing_price"],
                total=snapshot["total"],
                fee_amount=snapshot["fee_amount"],
                details={
                    "slug": snapshot["slug"],
                    "recipient_spec": recipient,
                    "recipient_id": snapshot["recipient_id"],
                },
                balance=snapshot["balance"],
            )
        except (GiftPurchaseError, DuplicatePurchaseError) as exc:
            await update.effective_message.reply_text(str(exc))
        except Exception as exc:
            logger.warning("Collectible gift preview failed (%s)", type(exc).__name__)
            await update.effective_message.reply_text(
                "Telegram could not verify this collectible or prepare its checkout. No purchase was attempted."
            )

    async def _authorized_private(self, update: Update) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or user.id != self.owner_id:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "This purchase tool is restricted to the bot owner."
                )
            return False
        if chat is None or chat.type != "private":
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Use gift purchase commands in the bot's private chat only."
                )
            return False
        return True

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        match = re.fullmatch(r"giftpurchase:(confirm|cancel):([0-9a-f]{16})", query.data or "")
        if not match:
            return
        action, request_id = match.groups()
        if update.effective_user is None or update.effective_user.id != self.owner_id:
            await query.answer("Only the bot owner can approve this purchase.", show_alert=True)
            return
        if query.message is None or query.message.chat.type != "private":
            await query.answer("Approve purchases only in the bot's private chat.", show_alert=True)
            return

        row = self.store.get(request_id)
        if (
            row is None
            or int(row["owner_id"]) != self.owner_id
            or int(row["chat_id"]) != query.message.chat.id
        ):
            await query.edit_message_text("This purchase approval is not valid.")
            return

        if action == "cancel":
            status, _ = self.store.claim(request_id)
            if status != "claimed":
                await query.edit_message_text(f"This request is already {status}.")
                return
            self.store.finish(request_id, "cancelled", "Owner cancelled before purchase.")
            await query.edit_message_text("Purchase cancelled. No funds were spent.")
            return

        async with self._purchase_lock:
            status, row = self.store.claim(request_id)
            if status != "claimed" or row is None:
                await query.edit_message_text(f"This request is already {status}. No purchase was sent.")
                return
            await self._execute_purchase(query, context, row)

    async def _execute_purchase(self, query, context, row: dict) -> None:
        request_id = row["request_id"]
        if row["source"] == "bot_api":
            await self._execute_bot_purchase(query, context, row)
        elif row["source"] == "mtproto":
            await self._execute_market_purchase(query, row)
        else:
            self.store.finish(request_id, "failed", "Unrecognized purchase source.")
            await query.edit_message_text("Purchase could not be processed. No funds were spent.")

    async def _execute_bot_purchase(self, query, context, row: dict) -> None:
        request_id = row["request_id"]
        gift_id = json.loads(row["details_json"]).get("gift_id", "")
        try:
            catalog = await context.bot.get_available_gifts()
            gift = next(
                (entry for entry in (getattr(catalog, "gifts", []) or []) if str(entry.id) == gift_id),
                None,
            )
            if (
                gift is None
                or getattr(gift, "remaining_count", None) == 0
                or getattr(gift, "personal_remaining_count", None) == 0
            ):
                self.store.finish(request_id, "failed", "Gift no longer available.")
                await query.edit_message_text("This gift is no longer available. No funds were spent.")
                return

            current_price = Decimal(int(gift.star_count))
            balance = stars_decimal(await context.bot.get_my_star_balance())
            if current_price != Decimal(row["approved_total"]):
                self.store.finish(request_id, "price_changed", "Bot gift price changed.")
                await query.edit_message_text(
                    "The gift price changed. A fresh preview follows; no purchase was made."
                )
                await self._prepare_bot_purchase_from_callback(
                    query, context, gift, int(row["recipient"]), balance
                )
                return
            if balance < current_price:
                self.store.finish(request_id, "failed", "Insufficient bot Stars balance.")
                await query.edit_message_text(
                    f"The bot now has {format_stars(balance)} Stars, below the "
                    f"{format_stars(current_price)} Star price. No funds were spent."
                )
                return

            sent = await context.bot.send_gift(
                gift_id=gift_id,
                user_id=int(row["recipient"]),
            )
            if sent is True:
                self.store.finish(request_id, "succeeded", "Telegram confirmed sendGift.")
                await query.edit_message_text(
                    f"Telegram confirmed the gift was sent to {row['recipient_name']}."
                )
            else:
                self.store.finish(request_id, "failed", "Telegram did not confirm the gift.")
                await query.edit_message_text(
                    "Telegram did not confirm the gift. Check the recipient before trying again."
                )
        except (TimedOut, NetworkError, asyncio.TimeoutError, TimeoutError, ConnectionError, OSError) as exc:
            logger.warning("Bot gift result is uncertain (%s)", type(exc).__name__)
            self.store.finish(request_id, "unknown", "Transport outcome uncertain; do not retry automatically.")
            await query.edit_message_text(
                "Telegram's response timed out, so the result is uncertain. Check the recipient's gifts "
                "and the bot's Stars balance before submitting another purchase. The bot will not retry."
            )
        except (BadRequest, TelegramError) as exc:
            logger.warning("Bot gift purchase rejected (%s)", type(exc).__name__)
            self.store.finish(request_id, "failed", "Telegram rejected the sendGift request.")
            await query.edit_message_text(
                "Telegram rejected the gift request. No successful purchase was reported."
            )
        except Exception as exc:
            logger.warning("Bot gift result is uncertain (%s)", type(exc).__name__)
            self.store.finish(request_id, "unknown", "Unexpected result; do not retry automatically.")
            await query.edit_message_text(
                "The result could not be verified. Check the recipient's gifts and the bot's Stars "
                "balance before trying again. The bot will not retry."
            )

    async def _prepare_bot_purchase_from_callback(
        self, query, context, gift, recipient_id: int, balance: Decimal
    ) -> None:
        price = Decimal(int(gift.star_count))
        if balance < price:
            await query.edit_message_text("The price changed and the bot balance is now insufficient. No purchase was made.")
            return
        title = f"Telegram gift ID {gift.id}"
        try:
            await self._send_preview(
                message=query.message,
                source="bot_api",
                chat_id=query.message.chat.id,
                owner_id=self.owner_id,
                item_key=str(gift.id),
                item_title=title,
                recipient=str(recipient_id),
                recipient_name=f"Telegram user {recipient_id}",
                listing_price=price,
                total=price,
                fee_amount=Decimal(0),
                details={"gift_id": str(gift.id)},
                balance=balance,
            )
        except DuplicatePurchaseError:
            await query.edit_message_text(
                "The price changed, but another request for this gift and recipient is already pending."
            )

    async def _execute_market_purchase(self, query, row: dict) -> None:
        request_id = row["request_id"]
        try:
            snapshot = await self._refresh_market_snapshot(row)
            approved_listing = Decimal(row["approved_listing_price"])
            approved_total = Decimal(row["approved_total"])
            if (
                snapshot["listing_price"] != approved_listing
                or snapshot["total"] != approved_total
            ):
                self.store.finish(request_id, "price_changed", "Marketplace or checkout price changed.")
                await self._prepare_market_from_callback(query, snapshot, row)
                return
            if snapshot["balance"] < snapshot["total"]:
                self.store.finish(request_id, "failed", "Insufficient authorized-account Stars balance.")
                await query.edit_message_text(
                    "The authorized account no longer has enough Stars. No purchase was made."
                )
                return

            from telethon import functions

            result = await self._market_client(
                functions.payments.SendStarsFormRequest(
                    form_id=snapshot["form_id"],
                    invoice=snapshot["invoice"],
                )
            )
            if result is not None:
                self.store.finish(request_id, "succeeded", "Telegram confirmed the Stars purchase.")
                await query.edit_message_text(
                    f"Telegram confirmed the collectible purchase for {row['recipient_name']}."
                )
            else:
                self.store.finish(request_id, "unknown", "Telegram returned no verifiable purchase result.")
                await query.edit_message_text(
                    "Telegram returned no verifiable purchase result. Check the authorized account before retrying."
                )
        except GiftPurchaseError as exc:
            self.store.finish(request_id, "failed", str(exc))
            await query.edit_message_text(f"{exc} No purchase was made.")
        except (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError) as exc:
            logger.warning("Collectible purchase result is uncertain (%s)", type(exc).__name__)
            self.store.finish(request_id, "unknown", "Transport outcome uncertain; do not retry automatically.")
            await query.edit_message_text(
                "Telegram's response timed out, so the result is uncertain. Check the authorized account's "
                "gift ownership and Stars balance before submitting another request. The bot will not retry."
            )
        except Exception as exc:
            error_name = type(exc).__name__
            logger.warning("Collectible purchase request failed (%s)", error_name)
            if error_name.endswith("RPCError"):
                self.store.finish(request_id, "failed", "Telegram rejected the purchase.")
                await query.edit_message_text(
                    "Telegram rejected the collectible purchase. No successful purchase was reported."
                )
            else:
                self.store.finish(request_id, "unknown", "Purchase result could not be verified.")
                await query.edit_message_text(
                    "The purchase result could not be verified. Check the authorized account before retrying. "
                    "The bot will not retry automatically."
                )

    async def _prepare_market_from_callback(self, query, snapshot: dict, row: dict) -> None:
        try:
            await query.edit_message_text(
                "The collectible price or checkout total changed. A fresh preview follows; no purchase was made."
            )
            await self._send_preview(
                message=query.message,
                source="mtproto",
                chat_id=query.message.chat.id,
                owner_id=self.owner_id,
                item_key=snapshot["slug"],
                item_title=snapshot["item_title"],
                recipient=row["recipient"],
                recipient_name=snapshot["recipient_name"],
                listing_price=snapshot["listing_price"],
                total=snapshot["total"],
                fee_amount=snapshot["fee_amount"],
                details={
                    "slug": snapshot["slug"],
                    "recipient_spec": row["recipient"],
                    "recipient_id": snapshot["recipient_id"],
                },
                balance=snapshot["balance"],
            )
        except DuplicatePurchaseError:
            await query.edit_message_text(
                "The price changed, but another request for this collectible is already pending."
            )


def register_gift_purchase_handlers(application: Application, owner_id: int) -> GiftPurchaseService:
    service = GiftPurchaseService(owner_id)
    application.bot_data["gift_purchase_service"] = service
    application.add_handler(CommandHandler("gifts", service.gifts_command))
    application.add_handler(CommandHandler("giftbuy", service.giftbuy_command))
    application.add_handler(
        CallbackQueryHandler(service.callback, pattern=CALLBACK_PATTERN)
    )

    previous_shutdown = application.post_shutdown

    async def _gift_purchase_shutdown(app: Application) -> None:
        if previous_shutdown is not None:
            await previous_shutdown(app)
        await service.shutdown()

    application.post_shutdown = _gift_purchase_shutdown
    return service
