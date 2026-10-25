"""
crash_server.py — Rollers Crash game server
Fully integrated with casino_data.json (same balances, profiles, history as the bot).
"""

import os, time, random, hashlib, threading, json, fcntl
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

BASE_DIR  = Path(__file__).parent
DATA_FILE = BASE_DIR / "casino_data.json"

app = Flask(__name__, static_folder=str(BASE_DIR))
CORS(app)
PORT = int(os.environ.get("PORT", 5000))

# ══════════════════════════════════════════════════════════════
#  casino_data.json integration
#  All reads/writes go through the same file the Telegram bot uses.
#  File-level locking prevents corruption when both processes write.
# ══════════════════════════════════════════════════════════════

_file_lock = threading.Lock()   # in-process serialiser

def _load_raw() -> dict:
    """Read casino_data.json and return the 'current' sub-dict."""
    try:
        with open(DATA_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            raw = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
        return raw.get("current", raw)
    except Exception as e:
        print(f"[crash] load error: {e}")
        return {}

def _save_raw(data: dict):
    """Write back to casino_data.json safely (exclusive lock, atomic replace)."""
    tmp = DATA_FILE.with_suffix(".tmp")
    try:
        # Read full file so we preserve all top-level keys (backup_data, etc.)
        with open(DATA_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            full = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
    except Exception:
        full = {}

    if "current" in full:
        full["current"].update(data)
    else:
        full.update(data)

    with open(tmp, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(full, f, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f, fcntl.LOCK_UN)
    os.replace(tmp, DATA_FILE)      # atomic on Linux

# ── balance helpers ──────────────────────────────────────────

def get_balance(uid: str) -> float:
    data = _load_raw()
    return float(data.get("user_balances", {}).get(str(uid), 0.0))

def _transact(uid: str, delta: float) -> tuple[bool, float]:
    """
    Atomically apply +/- delta to user's balance in casino_data.json.
    Returns (success, new_balance).
    A negative delta is a deduction; it fails if balance would go below 0.
    """
    uid = str(uid)
    with _file_lock:
        data = _load_raw()
        balances = data.get("user_balances", {})
        current  = float(balances.get(uid, 0.0))
        new_bal  = round(current + delta, 4)
        if new_bal < 0:
            return False, current
        balances[uid] = new_bal
        _save_raw({"user_balances": balances})
        return True, new_bal

# ── profile helpers ──────────────────────────────────────────

def get_profile(uid: str) -> dict:
    data = _load_raw()
    return dict(data.get("user_profiles", {}).get(str(uid), {}))

def upsert_profile(uid: str, updates: dict):
    uid = str(uid)
    with _file_lock:
        data     = _load_raw()
        profiles = data.get("user_profiles", {})
        p        = profiles.get(uid, {})
        p.update(updates)
        profiles[uid] = p
        _save_raw({"user_profiles": profiles})

# ── match history ────────────────────────────────────────────

def record_match(uid: str, bet: float, result: str, winnings: float, crash_at: float, cashout_mult: float | None):
    """Append to user_match_history and update lifetime counters — same format as the bot."""
    uid = str(uid)
    with _file_lock:
        data    = _load_raw()
        history = data.get("user_match_history", {})
        profiles = data.get("user_profiles", {})

        entry = {
            "timestamp": int(time.time()),
            "id":        str(int(time.time() * 1000))[-6:],
            "game":      "crash",
            "bet":       bet,
            "result":    result,
            "winnings":  winnings,
            "profit":    round(winnings - bet, 4) if result == "win" else round(-bet, 4),
            "crash_at":  crash_at,
            "cashout_mult": cashout_mult,
        }

        if uid not in history:
            history[uid] = []
        history[uid].append(entry)
        if len(history[uid]) > 200:
            history[uid] = history[uid][-200:]

        # update lifetime counters in user_profiles
        p = profiles.get(uid, {})
        p["lt_games"]   = p.get("lt_games",   0)   + 1
        p["lt_wagered"] = p.get("lt_wagered",  0.0) + bet
        p.setdefault("lt_wins", 0)
        p.setdefault("lt_won",  0.0)
        if result == "win":
            p["lt_wins"] = p["lt_wins"] + 1
            p["lt_won"]  = round(p["lt_won"] + winnings, 4)
        if "lt_first_game_ts" not in p:
            p["lt_first_game_ts"] = int(time.time())
        p["lt_last_game_ts"] = int(time.time())
        profiles[uid] = p

        _save_raw({"user_match_history": history, "user_profiles": profiles})

# ══════════════════════════════════════════════════════════════
#  Crash game engine
# ══════════════════════════════════════════════════════════════

_CRASH_MIN_BET    = 0.10
_CRASH_MAX_BET    = 1000.0
_CRASH_HOUSE_EDGE = 0.06    # 6 % — casino keeps 6 c on every dollar wagered
_CRASH_MAX_MULT   = 100.0   # cap so rounds never drag > ~20 s
_CRASH_BET_WINDOW = 7.0
_CRASH_PAUSE      = 4.0

_crash_state: dict = {
    "phase":      "betting",
    "multiplier": 1.00,
    "crash_at":   None,
    "bets":       {},           # {uid: bet_dict}
    "history":    [],           # last 20 crash points
    "countdown":  _CRASH_BET_WINDOW,
    "round_id":   0,
}
_crash_lock = threading.Lock()

# ── provably fair crash point ────────────────────────────────
# P(crash > M) = (1 - HOUSE_EDGE) / M  →  EV = -HOUSE_EDGE for any cashout target

def _crash_point(server_seed: str, nonce: int) -> float:
    h        = hashlib.sha256(f"{server_seed}:{nonce}:rollers_crash".encode()).hexdigest()
    hash_int = int(h[:8], 16)
    r        = hash_int / 0xFFFF_FFFF
    if r < _CRASH_HOUSE_EDGE:
        return 1.00
    raw = (1.0 - _CRASH_HOUSE_EDGE) / (1.0 - r)
    return min(round(raw, 2), _CRASH_MAX_MULT)

def _new_seed() -> str:
    return hashlib.sha256(f"{time.time()}-{random.random()}".encode()).hexdigest()[:32]

def _async_credit(uid: str, delta: float, bet: float, result: str,
                  winnings: float, crash_at: float, cashout_mult=None):
    try:
        _transact(uid, delta)
        record_match(uid, bet, result, winnings, crash_at, cashout_mult)
    except Exception as e:
        print(f"[crash] credit error uid={uid}: {e}")

# ── game loop ────────────────────────────────────────────────

def _game_loop():
    seed  = _new_seed()
    nonce = 0
    while True:
        crash_at = _crash_point(seed, nonce)

        with _crash_lock:
            _crash_state.update({
                "phase":      "betting",
                "multiplier": 1.00,
                "crash_at":   crash_at,
                "bets":       {},
                "countdown":  _CRASH_BET_WINDOW,
                "round_id":   _crash_state["round_id"] + 1,
            })

        # betting window
        t0 = time.time()
        while time.time() - t0 < _CRASH_BET_WINDOW:
            with _crash_lock:
                _crash_state["countdown"] = max(0.0, _CRASH_BET_WINDOW - (time.time() - t0))
            time.sleep(0.1)

        with _crash_lock:
            _crash_state["phase"]      = "flying"
            _crash_state["multiplier"] = 1.00

        # flying phase — tick every 50 ms for auto-cashout checks
        fly_start = time.time()
        while True:
            elapsed = time.time() - fly_start
            mult    = round(1.0022 ** (elapsed * 100), 2)

            with _crash_lock:
                _crash_state["multiplier"] = mult
                bets_snap = dict(_crash_state["bets"])

            # auto-cashout check
            for uid, bd in bets_snap.items():
                if bd.get("cashedout") or bd.get("_pending"):
                    continue
                ac = bd.get("auto_cashout")
                if ac and mult >= ac:
                    wins   = round(bd["amount"] * ac, 2)
                    profit = round(wins - bd["amount"], 2)
                    did_co = False
                    with _crash_lock:
                        entry = _crash_state["bets"].get(uid, {})
                        if not entry.get("cashedout"):
                            _crash_state["bets"][uid]["cashedout"]    = True
                            _crash_state["bets"][uid]["cashout_mult"] = ac
                            did_co = True
                    if did_co:
                        threading.Thread(
                            target=_async_credit,
                            args=(uid, wins, bd["amount"], "win", wins, crash_at, ac),
                            daemon=True
                        ).start()

            if mult >= crash_at:
                with _crash_lock:
                    _crash_state["phase"]      = "crashed"
                    _crash_state["multiplier"] = round(crash_at, 2)
                break
            time.sleep(0.05)

        # resolve all unresolved bets as losses
        with _crash_lock:
            hist = _crash_state.get("history", [])
            hist.append(round(crash_at, 2))
            _crash_state["history"] = hist[-20:]
            for uid, bd in _crash_state["bets"].items():
                if not bd.get("cashedout") and not bd.get("_pending"):
                    _crash_state["bets"][uid]["lost"] = True
                    threading.Thread(
                        target=_async_credit,
                        args=(uid, 0, bd["amount"], "loss", 0, crash_at, None),
                        daemon=True
                    ).start()

        time.sleep(_CRASH_PAUSE)
        seed  = _new_seed()
        nonce += 1

threading.Thread(target=_game_loop, daemon=True, name="CrashLoop").start()
print("🚀 Crash game loop started")

# ══════════════════════════════════════════════════════════════
#  Flask routes
# ══════════════════════════════════════════════════════════════

@app.route("/crash")
@app.route("/crash/<user_id>")
def crash_page(user_id=None):
    resp = app.make_response(send_from_directory(str(BASE_DIR), "crash.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp

# ── register / sync Telegram profile ────────────────────────
@app.route("/crash-api/register", methods=["POST"])
def api_register():
    """Called by the frontend on page load with Telegram user data."""
    d   = request.get_json(silent=True) or {}
    uid = str(d.get("user_id", "")).strip()
    if not uid:
        return jsonify({"ok": False, "error": "Missing user_id"})
    updates = {}
    for field in ("username", "first_name", "last_name", "photo_url"):
        if d.get(field):
            updates[field] = d[field]
    if updates:
        upsert_profile(uid, updates)
    # Return profile + balance in one shot
    prof    = get_profile(uid)
    balance = get_balance(uid)
    uname   = prof.get("username", "")
    fname   = prof.get("first_name", "")
    dn      = f"@{uname}" if uname else fname or f"Player{uid[-4:]}"
    return jsonify({
        "ok":           True,
        "user_id":      uid,
        "display_name": dn,
        "balance":      balance,
        "photo_url":    prof.get("photo_url", ""),
        "lt_games":     prof.get("lt_games",   0),
        "lt_wins":      prof.get("lt_wins",    0),
        "lt_wagered":   prof.get("lt_wagered", 0.0),
    })

# ── state ────────────────────────────────────────────────────
@app.route("/crash-api/state")
def api_state():
    with _crash_lock:
        snap = {
            "phase":      _crash_state["phase"],
            "multiplier": _crash_state["multiplier"],
            "bets":       dict(_crash_state["bets"]),
            "history":    list(_crash_state["history"]),
            "countdown":  _crash_state["countdown"],
            "round_id":   _crash_state["round_id"],
        }
        if _crash_state["phase"] == "crashed":
            snap["crash_at"] = _crash_state["crash_at"]
    return jsonify(snap)

# ── bet ───────────────────────────────────────────────────────
@app.route("/crash-api/bet", methods=["POST"])
def api_bet():
    d   = request.get_json(silent=True) or {}
    uid = str(d.get("user_id", "")).strip()
    try:   amount = round(float(d.get("amount", 0)), 2)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid amount"})
    try:   auto_co = float(d["auto_cashout"]) if d.get("auto_cashout") else None
    except (TypeError, ValueError):
        auto_co = None

    if not uid:
        return jsonify({"ok": False, "error": "Missing user_id"})
    if amount < _CRASH_MIN_BET:
        return jsonify({"ok": False, "error": f"Minimum bet is ${_CRASH_MIN_BET:.2f}"})
    if amount > _CRASH_MAX_BET:
        return jsonify({"ok": False, "error": f"Maximum bet is ${_CRASH_MAX_BET:,.0f}"})

    # Reserve slot first (prevents double-bet)
    SENTINEL = {"_pending": True, "amount": amount}
    with _crash_lock:
        if _crash_state["phase"] != "betting":
            return jsonify({"ok": False, "error": "Betting phase is over"})
        if uid in _crash_state["bets"]:
            return jsonify({"ok": False, "error": "You already have a bet this round"})
        _crash_state["bets"][uid] = SENTINEL

    # Deduct from casino_data.json
    ok, _ = _transact(uid, -amount)
    if not ok:
        with _crash_lock:
            if _crash_state["bets"].get(uid) is SENTINEL:
                del _crash_state["bets"][uid]
        return jsonify({"ok": False, "error": "Insufficient balance"})

    # Confirm bet
    prof  = get_profile(uid)
    uname = prof.get("username", "")
    fname = prof.get("first_name", "")
    dn    = f"@{uname}" if uname else fname or f"Player{uid[-4:]}"

    with _crash_lock:
        if _crash_state["phase"] != "betting":
            _transact(uid, +amount)        # refund
            if _crash_state["bets"].get(uid) is SENTINEL:
                del _crash_state["bets"][uid]
            return jsonify({"ok": False, "error": "Round started — bet refunded"})
        _crash_state["bets"][uid] = {
            "amount":       amount,
            "cashedout":    False,
            "cashout_mult": None,
            "lost":         False,
            "auto_cashout": auto_co,
            "username":     uname,
            "display_name": dn,
            "photo_url":    prof.get("photo_url", ""),
        }

    return jsonify({"ok": True})

# ── cashout ───────────────────────────────────────────────────
@app.route("/crash-api/cashout", methods=["POST"])
def api_cashout():
    d   = request.get_json(silent=True) or {}
    uid = str(d.get("user_id", "")).strip()
    if not uid:
        return jsonify({"ok": False, "error": "Missing user_id"})

    with _crash_lock:
        if _crash_state["phase"] != "flying":
            return jsonify({"ok": False, "error": "Not in flying phase"})
        bd = _crash_state["bets"].get(uid)
        if not bd or bd.get("_pending"):
            return jsonify({"ok": False, "error": "No active bet"})
        if bd.get("cashedout"):
            return jsonify({"ok": False, "error": "Already cashed out"})
        mult     = _crash_state["multiplier"]
        amount   = bd["amount"]
        crash_at = _crash_state["crash_at"]
        winnings = round(amount * mult, 2)
        _crash_state["bets"][uid]["cashedout"]    = True
        _crash_state["bets"][uid]["cashout_mult"] = mult

    threading.Thread(
        target=_async_credit,
        args=(uid, winnings, amount, "win", winnings, crash_at, mult),
        daemon=True
    ).start()
    return jsonify({"ok": True, "multiplier": mult, "winnings": winnings})

# ── profile ───────────────────────────────────────────────────
@app.route("/crash-api/profile/<user_id>")
def api_profile(user_id):
    uid   = str(user_id)
    prof  = get_profile(uid)
    bal   = get_balance(uid)
    uname = prof.get("username", "")
    fname = prof.get("first_name", "")
    dn    = f"@{uname}" if uname else fname or f"Player{uid[-4:]}"
    return jsonify({
        "user_id":      uid,
        "display_name": dn,
        "balance":      bal,
        "photo_url":    prof.get("photo_url", ""),
        "lt_games":     prof.get("lt_games",   0),
        "lt_wins":      prof.get("lt_wins",    0),
        "lt_wagered":   prof.get("lt_wagered", 0.0),
    })

# ── history ───────────────────────────────────────────────────
@app.route("/crash-api/history/<user_id>")
def api_history(user_id):
    uid  = str(user_id)
    data = _load_raw()
    matches = data.get("user_match_history", {}).get(uid, [])
    crash_matches = [m for m in matches if m.get("game") == "crash"][-50:]
    return jsonify({"ok": True, "history": crash_matches})

@app.route("/")
def root():
    return '<meta http-equiv="refresh" content="0; url=/crash">'

if __name__ == "__main__":
    print(f"🎰 Crash server on :{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
