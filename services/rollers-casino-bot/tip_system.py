"""Tip system for Rollers Casino bot."""
import sqlite3
import logging

logger = logging.getLogger(__name__)

_DB = "tip_system.db"

_prefs: dict = {}


def init_tip_database():
    """Initialise the tip system database."""
    try:
        conn = sqlite3.connect(_DB)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_prefs (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                currency   TEXT DEFAULT 'USDT'
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tips (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id    INTEGER,
                receiver_id  INTEGER,
                amount       REAL,
                currency     TEXT,
                timestamp    REAL
            )
        """)
        conn.commit()
        conn.close()
        logger.info("Tip database initialised.")
    except Exception as e:
        logger.error(f"init_tip_database error: {e}")


def register_tip_handlers(application):
    """Register tip-related command handlers (stub — handled in main.py)."""
    pass


def set_user_pref(user_id: int, username: str, currency: str):
    """Save user currency preference."""
    try:
        uid = int(user_id)
        _prefs[uid] = {"username": username, "currency": currency or "USDT"}
        conn = sqlite3.connect(_DB)
        c = conn.cursor()
        c.execute("""
            INSERT INTO user_prefs (user_id, username, currency)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                                               currency=excluded.currency
        """, (uid, username or "", currency or "USDT"))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"set_user_pref error: {e}")


def get_preferred_currency(user_id) -> str:
    """Return the user's preferred currency (default USDT)."""
    try:
        uid = int(user_id)
        if uid in _prefs:
            return _prefs[uid].get("currency") or "USDT"
        conn = sqlite3.connect(_DB)
        c = conn.cursor()
        c.execute("SELECT currency FROM user_prefs WHERE user_id=?", (uid,))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception as e:
        logger.error(f"get_preferred_currency error: {e}")
    return "USDT"


async def tip(update, context):
    """Handle /tip command — stub, main logic is in main.py."""
    pass
