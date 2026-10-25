"""
ops.py — Production Operational Safety Module for Rollers Casino Bot
=====================================================================
Provides:
  1. Rate limiting / anti-spam (per-user, per-action sliding window)
  2. Admin audit log  (audit_log.json — append-only)
  3. Owner alert system (Telegram DM to owner/managers)
  4. Scheduled DB backups with retention (backups/ dir)
  5. Centralised error/health monitor (error_log.json)
  6. Recovery protection helpers (detect in-flight operations on startup)

Design rules:
  • Zero mandatory dependencies on main.py at import time.
  • All main.py hooks are optional — if ops fails, bot continues.
  • Every public function is try/except-safe; exceptions are logged, never raised.
  • Thread-safe (uses threading.Lock where needed).
"""

import os
import json
import time
import shutil
import logging
import threading
import traceback
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)

# ─── Paths ────────────────────────────────────────────────────────────────────

_BASE_DIR       = Path(__file__).parent
BACKUP_DIR      = _BASE_DIR / "backups"
AUDIT_LOG_FILE  = _BASE_DIR / "audit_log.json"
ERROR_LOG_FILE  = _BASE_DIR / "error_log.json"

BACKUP_DIR.mkdir(exist_ok=True)

# ─── Config ───────────────────────────────────────────────────────────────────

BACKUP_INTERVAL_SEC   = 3600        # backup every hour
BACKUP_RETAIN_COUNT   = 48          # keep last 48 hourly backups (~2 days)
ALERT_ERROR_THRESHOLD = 5           # alert after N errors of same type in window
ALERT_WINDOW_SEC      = 300         # error-count window (5 min)
BALANCE_SPIKE_USD     = 5000.0      # alert if single credit/debit > this

# Files to back up on each cycle
BACKUP_TARGETS = [
    "casino_data.json",
    "balances_backup.json",
    "active_rains.json",
    "transaction_log.json",
]

# ─── Rate-limit config ────────────────────────────────────────────────────────
# Each entry: (max_calls, window_seconds)
_RL_LIMITS = {
    "callback":    (25, 10),    # 25 button taps per 10 s
    "deposit":     (5,  60),    # 5 deposit actions per 60 s
    "withdrawal":  (3,  60),    # 3 withdrawal actions per 60 s
    "ai":          (5,  30),    # 5 AI queries per 30 s
    "command":     (15, 10),    # 15 commands per 10 s
}

# ─── Internal state ───────────────────────────────────────────────────────────

_rl_lock    = threading.Lock()
_audit_lock = threading.Lock()
_error_lock = threading.Lock()

# {action: {user_id: deque([timestamp, ...])}}
_rl_windows: dict = defaultdict(lambda: defaultdict(deque))

# {error_type: deque([timestamp, ...])}
_error_windows: dict = defaultdict(deque)
# {error_type: bool}  — track whether we've already alerted for this burst
_alerted: dict = {}

# Bot instance — set via ops.set_bot(bot, owner_id, manager_ids)
_bot          = None
_owner_id     = None
_manager_ids: set = set()
_alert_lock   = threading.Lock()

# ─── Initialisation ───────────────────────────────────────────────────────────

def set_bot(bot, owner_id: int, manager_ids: Optional[set] = None):
    """Call once from main.py after the Application is built."""
    global _bot, _owner_id, _manager_ids
    _bot        = bot
    _owner_id   = int(owner_id)
    _manager_ids = set(manager_ids or [])
    logger.info(f"[OPS] Bot reference set. Owner: {owner_id}")


# ─── 1. Rate limiter ──────────────────────────────────────────────────────────

def check_rate_limit(user_id: str, action: str) -> bool:
    """
    Returns True if the user is within limits (allowed).
    Returns False if the user is rate-limited (block the action).
    """
    try:
        limits = _RL_LIMITS.get(action)
        if not limits:
            return True
        max_calls, window_sec = limits
        now = time.monotonic()
        uid = str(user_id)
        with _rl_lock:
            dq = _rl_windows[action][uid]
            # Evict timestamps outside the window
            cutoff = now - window_sec
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= max_calls:
                return False   # rate-limited
            dq.append(now)
            return True
    except Exception as e:
        logger.debug(f"[OPS] rate_limit check error: {e}")
        return True   # fail-open — never block on ops error


def rl_cleanup():
    """Periodically purge empty buckets to prevent memory growth (call every ~10 min)."""
    try:
        now = time.monotonic()
        with _rl_lock:
            for action, users in list(_rl_windows.items()):
                for uid, dq in list(users.items()):
                    limits = _RL_LIMITS.get(action, (0, 60))
                    cutoff = now - limits[1]
                    while dq and dq[0] < cutoff:
                        dq.popleft()
                    if not dq:
                        del users[uid]
    except Exception:
        pass


# ─── 2. Admin audit log ───────────────────────────────────────────────────────

def audit(action: str, admin_id, target_id=None, details: Optional[dict] = None):
    """
    Append one entry to audit_log.json.
    Fields: ts, action, admin_id, target_id, details.
    """
    try:
        entry = {
            "ts":        datetime.now(timezone.utc).isoformat(),
            "action":    action,
            "admin_id":  str(admin_id),
            "target_id": str(target_id) if target_id is not None else None,
            "details":   details or {},
        }
        with _audit_lock:
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        logger.info(f"[AUDIT] {action} | admin={admin_id} target={target_id} | {details}")
    except Exception as e:
        logger.warning(f"[OPS] audit write failed: {e}")


def get_audit_log(limit: int = 50, action_filter: Optional[str] = None) -> List[dict]:
    """Read the last `limit` audit entries, optionally filtered by action prefix."""
    try:
        if not AUDIT_LOG_FILE.exists():
            return []
        entries = []
        with _audit_lock:
            with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if action_filter is None or action_filter in e.get("action", ""):
                            entries.append(e)
                    except Exception:
                        pass
        return entries[-limit:]
    except Exception as e:
        logger.warning(f"[OPS] get_audit_log error: {e}")
        return []


# ─── 3. Owner alert system ────────────────────────────────────────────────────

import asyncio

def alert_owner(message: str, level: str = "WARN"):
    """
    Fire-and-forget DM to owner (and optionally managers).
    Safe to call from sync or async context.
    """
    try:
        prefix = {"WARN": "⚠️", "ERROR": "🔴", "INFO": "ℹ️", "CRIT": "🚨"}.get(level, "⚠️")
        text = f"{prefix} <b>[{level}] Rollers Casino Alert</b>\n\n{message}"
        if _bot is None or _owner_id is None:
            logger.warning(f"[OPS] alert_owner called before bot set: {message}")
            return
        _send_alert_async(text)
    except Exception as e:
        logger.warning(f"[OPS] alert_owner error: {e}")


def _send_alert_async(text: str):
    """Schedule the Telegram send on the running event loop (non-blocking)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(_do_send_alert(text))
            )
        else:
            loop.run_until_complete(_do_send_alert(text))
    except Exception as e:
        logger.warning(f"[OPS] _send_alert_async: {e}")


async def _do_send_alert(text: str):
    try:
        if _bot and _owner_id:
            await _bot.send_message(chat_id=_owner_id, text=text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"[OPS] owner DM failed: {e}")


# ─── 4. Error / health monitor ────────────────────────────────────────────────

def log_error(error_type: str, message: str, exc: Optional[Exception] = None,
               alert: bool = True):
    """
    Log a structured error entry and optionally alert owner if bursting.
    error_type examples: "deposit", "settlement", "webhook", "db", "callback"
    """
    try:
        entry = {
            "ts":         datetime.now(timezone.utc).isoformat(),
            "type":       error_type,
            "message":    message,
            "traceback":  traceback.format_exc() if exc else None,
        }
        with _error_lock:
            with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

        logger.error(f"[OPS:{error_type}] {message}" + (f" | {exc}" if exc else ""))

        if alert:
            _check_error_threshold(error_type, message)
    except Exception as e:
        logger.warning(f"[OPS] log_error write failed: {e}")


def _check_error_threshold(error_type: str, message: str):
    """Alert owner if error_type exceeds ALERT_ERROR_THRESHOLD in ALERT_WINDOW_SEC."""
    try:
        now = time.monotonic()
        cutoff = now - ALERT_WINDOW_SEC
        with _error_lock:
            dq = _error_windows[error_type]
            while dq and dq[0] < cutoff:
                dq.popleft()
            dq.append(now)
            count = len(dq)
            already_alerted = _alerted.get(error_type, False)

        if count >= ALERT_ERROR_THRESHOLD and not already_alerted:
            with _error_lock:
                _alerted[error_type] = True
            alert_owner(
                f"<b>{count} {error_type} errors</b> in the last "
                f"{ALERT_WINDOW_SEC//60} min.\n\nLatest: {message[:300]}",
                level="ERROR"
            )
        elif count < ALERT_ERROR_THRESHOLD:
            with _error_lock:
                _alerted[error_type] = False   # reset so we alert again next burst
    except Exception as e:
        logger.debug(f"[OPS] _check_error_threshold: {e}")


def get_error_log(limit: int = 50, error_type: Optional[str] = None) -> List[dict]:
    """Read the last `limit` error log entries."""
    try:
        if not ERROR_LOG_FILE.exists():
            return []
        entries = []
        with _error_lock:
            with open(ERROR_LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if error_type is None or e.get("type") == error_type:
                            entries.append(e)
                    except Exception:
                        pass
        return entries[-limit:]
    except Exception as e:
        logger.warning(f"[OPS] get_error_log error: {e}")
        return []


# ─── Balance spike guard ──────────────────────────────────────────────────────

def check_balance_change(user_id: str, amount: float, direction: str,
                          tx_type: str):
    """
    Call after every balance credit/debit.
    Alerts owner on suspiciously large single changes.
    direction: "credit" or "debit"
    """
    try:
        if abs(amount) >= BALANCE_SPIKE_USD:
            alert_owner(
                f"💰 <b>Large balance {direction}</b>\n"
                f"User: <code>{user_id}</code>\n"
                f"Amount: <b>${amount:,.2f}</b>\n"
                f"Type: {tx_type}",
                level="WARN"
            )
    except Exception:
        pass


# ─── Duplicate payout guard ───────────────────────────────────────────────────

_seen_payouts: set = set()
_payout_lock  = threading.Lock()

def is_duplicate_payout(payout_key: str) -> bool:
    """
    Returns True and alerts if this payout_key has already been processed.
    payout_key: a unique string, e.g. f"{bet_id}:{user_id}:win"
    """
    try:
        with _payout_lock:
            if payout_key in _seen_payouts:
                alert_owner(
                    f"🚨 <b>Duplicate payout attempt blocked!</b>\n"
                    f"Key: <code>{payout_key}</code>",
                    level="CRIT"
                )
                log_error("duplicate_payout", f"Blocked: {payout_key}", alert=False)
                return True
            _seen_payouts.add(payout_key)
            # Prevent unbounded growth
            if len(_seen_payouts) > 50000:
                _seen_payouts.clear()
            return False
    except Exception:
        return False


# ─── 5. Scheduled DB backups ──────────────────────────────────────────────────

_last_backup_ts: float = 0.0
_backup_lock = threading.Lock()


def run_backup(label: str = "") -> bool:
    """
    Copy all BACKUP_TARGETS to backups/<timestamp>_<label>/ directory.
    Prunes old backups so only BACKUP_RETAIN_COUNT are kept.
    Returns True on success.
    """
    global _last_backup_ts
    try:
        with _backup_lock:
            ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            folder_name = f"{ts_str}_{label}" if label else ts_str
            dest = BACKUP_DIR / folder_name
            dest.mkdir(parents=True, exist_ok=True)

            copied = []
            failed = []
            for fname in BACKUP_TARGETS:
                src = _BASE_DIR / fname
                if src.exists():
                    try:
                        shutil.copy2(src, dest / fname)
                        copied.append(fname)
                    except Exception as e:
                        failed.append(f"{fname}:{e}")

            _last_backup_ts = time.time()

            # Write a manifest
            manifest = {
                "ts": ts_str, "label": label,
                "copied": copied, "failed": failed,
            }
            with open(dest / "_manifest.json", "w") as mf:
                json.dump(manifest, mf, indent=2)

            logger.info(f"[OPS] Backup complete → {dest.name} | "
                        f"copied={len(copied)} failed={len(failed)}")

            if failed:
                log_error("db_backup", f"Partial backup — failed: {failed}", alert=False)

            # Prune old backups
            _prune_backups()
            return True
    except Exception as e:
        log_error("db_backup", f"Backup failed: {e}", exc=e)
        alert_owner(f"🗄️ <b>DB backup failed!</b>\n{e}", level="ERROR")
        return False


def _prune_backups():
    """Keep only the most recent BACKUP_RETAIN_COUNT backup folders."""
    try:
        folders = sorted(
            [p for p in BACKUP_DIR.iterdir() if p.is_dir()],
            key=lambda p: p.name
        )
        excess = len(folders) - BACKUP_RETAIN_COUNT
        if excess > 0:
            for old in folders[:excess]:
                shutil.rmtree(old, ignore_errors=True)
                logger.debug(f"[OPS] Pruned old backup: {old.name}")
    except Exception as e:
        logger.warning(f"[OPS] _prune_backups error: {e}")


def get_last_backup_age_minutes() -> Optional[float]:
    """Return minutes since last backup, or None if no backup has run."""
    if _last_backup_ts == 0:
        # Check disk
        try:
            folders = sorted(
                [p for p in BACKUP_DIR.iterdir() if p.is_dir()],
                key=lambda p: p.stat().st_mtime
            )
            if folders:
                age = (time.time() - folders[-1].stat().st_mtime) / 60
                return round(age, 1)
        except Exception:
            pass
        return None
    return round((time.time() - _last_backup_ts) / 60, 1)


# ─── 6. Background scheduler ──────────────────────────────────────────────────

async def ops_scheduler():
    """
    Long-running coroutine — schedule from set_commands via asyncio.create_task.
    Handles: periodic backup, rate-limit cleanup, error-window reset.
    """
    logger.info("[OPS] Scheduler started")
    tick = 0
    while True:
        try:
            await asyncio.sleep(60)   # wake every minute
            tick += 1

            # Backup every BACKUP_INTERVAL_SEC seconds
            if tick % (BACKUP_INTERVAL_SEC // 60) == 0:
                await asyncio.to_thread(run_backup, "scheduled")

            # Rate-limit bucket cleanup every 10 min
            if tick % 10 == 0:
                await asyncio.to_thread(rl_cleanup)

            # Trim payout dedup set every hour
            if tick % 60 == 0:
                with _payout_lock:
                    if len(_seen_payouts) > 10000:
                        _seen_payouts.clear()

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[OPS] scheduler error: {e}")


# ─── 7. Recovery protection ───────────────────────────────────────────────────

def check_recovery(casino_data: dict) -> dict:
    """
    Called at startup with the loaded casino_data dict.
    Scans for signs of incomplete operations and returns a report.
    Does NOT modify any data — only reports what it finds.
    """
    report = {
        "suspicious_games":      [],
        "pending_withdrawals":   [],
        "warnings":              [],
    }
    try:
        # Detect active_games entries (in-flight games at shutdown)
        active = casino_data.get("active_games", {})
        if active:
            report["suspicious_games"] = list(active.keys())
            report["warnings"].append(
                f"{len(active)} active game(s) found in casino_data — may need cleanup"
            )

        # Detect pending withdrawals
        pw = casino_data.get("pending_crypto_withdrawals", {})
        if pw:
            report["pending_withdrawals"] = list(pw.keys())
            report["warnings"].append(
                f"{len(pw)} pending withdrawal(s) found"
            )

        if report["warnings"]:
            for w in report["warnings"]:
                logger.warning(f"[OPS:RECOVERY] {w}")
            alert_owner(
                "🔁 <b>Bot restarted — recovery check</b>\n\n"
                + "\n".join(f"• {w}" for w in report["warnings"]),
                level="INFO"
            )
    except Exception as e:
        logger.warning(f"[OPS] check_recovery error: {e}")
    return report


# ─── 8. Suspicious activity detector ─────────────────────────────────────────

_spam_window_sec = 30
_spam_threshold  = 50   # callbacks from same user in 30 s = suspicious
_spam_alerted: set = set()
_spam_lock = threading.Lock()
_spam_counts: dict = defaultdict(deque)


def check_spam(user_id: str, action: str = "callback") -> bool:
    """
    Returns True if user looks like spam/bot.
    Alerts owner on first detection per user.
    """
    try:
        now = time.monotonic()
        cutoff = now - _spam_window_sec
        uid = str(user_id)
        with _spam_lock:
            dq = _spam_counts[uid]
            while dq and dq[0] < cutoff:
                dq.popleft()
            dq.append(now)
            count = len(dq)
            already = uid in _spam_alerted

        if count >= _spam_threshold and not already:
            with _spam_lock:
                _spam_alerted.add(uid)
            alert_owner(
                f"🚨 <b>Suspicious spam activity</b>\n"
                f"User: <code>{uid}</code>\n"
                f"Action: {action}\n"
                f"Count: {count} in {_spam_window_sec}s",
                level="WARN"
            )
        return count >= _spam_threshold
    except Exception:
        return False


# ─── Convenience: one-line startup call ──────────────────────────────────────

def startup_backup():
    """Run an immediate backup on bot startup."""
    try:
        run_backup("startup")
    except Exception as e:
        logger.warning(f"[OPS] startup_backup: {e}")
