"""
events.py — Promotional & Event Hosting System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ACCESS: OWNER-ONLY (checked by caller before calling ev_* callbacks).
        Admins/managers have zero access to event creation or management.

Integration hooks (called from main.py):
    load_events(data_dict)
    save_events(data_dict)
    on_deposit(uid, usd, is_first, username)  → returns [(bonus_usd, title)]
    on_loss(uid, loss_usd)                    → cashback tracking
    handle_ev_callback(query, ctx, uid, data, callbacks)
"""

import time, logging, re, secrets, copy
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
_ev: dict = {
    "deposit_bonuses": {},
    "deposit_race":    {},
    "cashback_events": {},
    "stats": {
        "total_bonuses_paid":  0.0,
        "total_cashback_paid": 0.0,
        "total_race_prizes":   0.0,
    },
}

_OWNER_ID_STR: str = ""

def set_owner_id(oid):
    global _OWNER_ID_STR
    _OWNER_ID_STR = str(oid)

def is_owner(uid) -> bool:
    return str(uid) == _OWNER_ID_STR

def _now() -> float:
    return time.time()

def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(_now())}_{secrets.token_hex(3)}"

def _fmt_time_left(end_ts: float) -> str:
    secs = max(0, int(end_ts - _now()))
    if secs >= 86400: return f"{secs//86400}d {(secs%86400)//3600}h"
    if secs >= 3600:  return f"{secs//3600}h {(secs%3600)//60}m"
    if secs >= 60:    return f"{secs//60}m"
    return f"{secs}s"

def _fmt_ts(ts: float) -> str:
    import datetime
    return datetime.datetime.utcfromtimestamp(ts).strftime("%b %d %H:%M UTC")


# ── Persistence ───────────────────────────────────────────────────────────────
def load_events(data: dict):
    src = data.get("_events", {})
    _ev["deposit_bonuses"] = src.get("deposit_bonuses", {})
    _ev["deposit_race"]    = src.get("deposit_race",    {})
    _ev["cashback_events"] = src.get("cashback_events", {})
    _ev["stats"]           = src.get("stats", {
        "total_bonuses_paid": 0.0, "total_cashback_paid": 0.0, "total_race_prizes": 0.0,
    })
    for b in _ev["deposit_bonuses"].values():
        if isinstance(b.get("claimed_by"), list):
            b["claimed_by"] = set(b["claimed_by"])

def save_events(data: dict):
    snapshot = copy.deepcopy(_ev)
    for b in snapshot["deposit_bonuses"].values():
        if isinstance(b.get("claimed_by"), set):
            b["claimed_by"] = list(b["claimed_by"])
    data["_events"] = snapshot


# ── Deposit Bonus ─────────────────────────────────────────────────────────────
def create_deposit_bonus(type_: str, percentage: float, min_deposit: float,
                         max_bonus: float, title: str, hours: float) -> str:
    bid = _new_id("db")
    _ev["deposit_bonuses"][bid] = {
        "id":           bid,
        "type":         type_,
        "percentage":   percentage,
        "min_deposit":  min_deposit,
        "max_bonus":    max_bonus,
        "title":        title,
        "active":       True,
        "created_at":   _now(),
        "expires":      (_now() + hours * 3600) if hours > 0 else None,
        "claimed_by":   set(),
        "total_paid":   0.0,
        "total_claims": 0,
    }
    logger.info(f"[EVENTS] Deposit bonus created: {bid} type={type_} {percentage}% title={title}")
    return bid

def on_deposit(user_id: str, usd_amount: float, is_first_deposit: bool,
               username: str = "") -> list:
    """
    Called after every confirmed deposit.
    Returns [(bonus_usd, title)] for all applicable active bonuses.
    Caller must credit these amounts and save.
    """
    user_id = str(user_id)
    bonuses = []
    now = _now()
    import datetime as _dt
    wday = _dt.datetime.utcnow().weekday()
    is_weekend = wday in (5, 6)

    for bid, b in _ev["deposit_bonuses"].items():
        if not b.get("active"):
            continue
        if b.get("expires") and now > b["expires"]:
            continue
        if usd_amount < b.get("min_deposit", 0):
            continue
        btype = b.get("type", "percentage")

        if btype == "cashback":
            continue
        if btype == "weekend" and not is_weekend:
            continue
        if btype == "first_deposit":
            if not is_first_deposit:
                continue
            if user_id in b["claimed_by"]:
                continue

        bonus_usd = round(usd_amount * b["percentage"] / 100.0, 2)
        if b.get("max_bonus", 0) > 0:
            bonus_usd = min(bonus_usd, b["max_bonus"])
        if bonus_usd <= 0:
            continue

        b["claimed_by"].add(user_id)
        b["total_paid"]   = round(b.get("total_paid", 0.0) + bonus_usd, 2)
        b["total_claims"] = b.get("total_claims", 0) + 1
        _ev["stats"]["total_bonuses_paid"] = round(
            _ev["stats"].get("total_bonuses_paid", 0.0) + bonus_usd, 2
        )
        label = b.get("title", f"{b['percentage']:.0f}% Deposit Bonus")
        bonuses.append((bonus_usd, label))
        logger.info(f"[EVENTS] Deposit bonus applied: user={user_id} bid={bid} +${bonus_usd:.2f}")

    # Deposit race tracking
    race = _ev.get("deposit_race", {})
    if race.get("active") and now <= race.get("end_time", 0):
        deps = race.setdefault("deposits", {})
        deps[user_id] = round(deps.get(user_id, 0.0) + usd_amount, 2)
        race.setdefault("usernames", {})[user_id] = username or user_id
        race["total_deposited"] = round(race.get("total_deposited", 0.0) + usd_amount, 2)
        logger.info(f"[EVENTS] Deposit race updated: user={user_id} total=${deps[user_id]:.2f}")

    return bonuses


# ── Cashback ──────────────────────────────────────────────────────────────────
def create_cashback_event(percentage: float, hours: float, title: str,
                          min_loss: float = 10.0, max_cashback: float = 0.0) -> str:
    eid = _new_id("cb")
    _ev["cashback_events"][eid] = {
        "id":          eid,
        "type":        "cashback",
        "title":       title,
        "percentage":  percentage,
        "min_loss":    min_loss,
        "max_cashback": max_cashback,
        "active":      True,
        "start_time":  _now(),
        "end_time":    _now() + hours * 3600,
        "losses":      {},
        "total_paid":  0.0,
    }
    logger.info(f"[EVENTS] Cashback event created: {eid} {percentage}% {hours}h")
    return eid

def on_loss(user_id: str, loss_usd: float):
    if loss_usd <= 0:
        return
    user_id = str(user_id)
    now = _now()
    for ev in _ev["cashback_events"].values():
        if not ev.get("active") or now > ev.get("end_time", 0):
            continue
        ev["losses"][user_id] = round(ev["losses"].get(user_id, 0.0) + loss_usd, 2)

def end_cashback_event(event_id: str) -> list:
    ev = _ev["cashback_events"].get(event_id)
    if not ev:
        return []
    ev["active"] = False
    pct     = ev.get("percentage", 0) / 100.0
    min_l   = ev.get("min_loss", 10.0)
    max_cb  = ev.get("max_cashback", 0.0)
    payouts = []
    for uid, losses in ev.get("losses", {}).items():
        if losses < min_l:
            continue
        cb = round(losses * pct, 2)
        if max_cb > 0:
            cb = min(cb, max_cb)
        if cb > 0:
            payouts.append((uid, cb))
            ev["total_paid"] = round(ev.get("total_paid", 0.0) + cb, 2)
    _ev["stats"]["total_cashback_paid"] = round(
        _ev["stats"].get("total_cashback_paid", 0.0) + ev.get("total_paid", 0.0), 2
    )
    logger.info(f"[EVENTS] Cashback {event_id} ended: {len(payouts)} payouts")
    return payouts


# ── Deposit Race ──────────────────────────────────────────────────────────────
def start_deposit_race(prizes: list, hours: float, title: str) -> bool:
    if _ev.get("deposit_race", {}).get("active"):
        return False
    _ev["deposit_race"] = {
        "title":           title,
        "prizes":          prizes,
        "active":          True,
        "start_time":      _now(),
        "end_time":        _now() + hours * 3600,
        "deposits":        {},
        "usernames":       {},
        "total_deposited": 0.0,
    }
    logger.info(f"[EVENTS] Deposit race started: {title} prizes={prizes} {hours}h")
    return True

def end_deposit_race() -> list:
    race = _ev.get("deposit_race", {})
    if not race:
        return []
    race["active"] = False
    prizes      = race.get("prizes", [])
    deps        = race.get("deposits", {})
    usernames   = race.get("usernames", {})
    sorted_deps = sorted(deps.items(), key=lambda x: x[1], reverse=True)
    results     = []
    for i, (uid, total_dep) in enumerate(sorted_deps[:len(prizes)]):
        prize = prizes[i] if i < len(prizes) else 0.0
        if prize > 0:
            results.append((uid, usernames.get(uid, uid), total_dep, prize))
            _ev["stats"]["total_race_prizes"] = round(
                _ev["stats"].get("total_race_prizes", 0.0) + prize, 2
            )
    logger.info(f"[EVENTS] Deposit race ended: {len(results)} winners")
    return results

def get_deposit_race_leaderboard(limit: int = 10) -> list:
    race = _ev.get("deposit_race", {})
    deps  = race.get("deposits", {})
    names = race.get("usernames", {})
    return [
        (uid, names.get(uid, uid), amt)
        for uid, amt in sorted(deps.items(), key=lambda x: x[1], reverse=True)[:limit]
    ]


# ── Panel Builders ────────────────────────────────────────────────────────────
def build_events_panel() -> tuple:
    now  = _now()
    active_db  = [b for b in _ev["deposit_bonuses"].values()
                  if b.get("active") and (not b.get("expires") or now <= b["expires"])]
    active_cb  = [e for e in _ev["cashback_events"].values()
                  if e.get("active") and now <= e.get("end_time", 0)]
    race       = _ev.get("deposit_race", {})
    race_live  = race.get("active") and now <= race.get("end_time", 0)

    lines = ["🎯 <b>EVENTS &amp; PROMOS PANEL</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    lines.append(f"\n💰 Deposit Bonuses: <b>{len(active_db)} active</b>")
    for b in active_db[:3]:
        exp = f" ⏳{_fmt_time_left(b['expires'])}" if b.get("expires") else " (no expiry)"
        lines.append(f"  • {b['title']} — {b['percentage']:.0f}%{exp}")

    if race_live:
        lb    = get_deposit_race_leaderboard(1)
        lead  = f" | Leader: ${lb[0][2]:.0f}" if lb else ""
        lines.append(
            f"\n🏆 Deposit Race: <b>LIVE</b> ⏳{_fmt_time_left(race['end_time'])}{lead}\n"
            f"  {len(race.get('deposits',{}))} participants | ${race.get('total_deposited',0):.0f} total"
        )
    else:
        lines.append("\n🏆 Deposit Race: <b>Inactive</b>")

    lines.append(f"\n💸 Cashback Events: <b>{len(active_cb)} active</b>")
    for e in active_cb[:2]:
        lines.append(f"  • {e['title']} — {e['percentage']:.0f}% ⏳{_fmt_time_left(e['end_time'])}")

    stats = _ev.get("stats", {})
    lines.append(
        f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Total paid: Bonuses ${stats.get('total_bonuses_paid',0):.2f} | "
        f"Cashback ${stats.get('total_cashback_paid',0):.2f} | "
        f"Race ${stats.get('total_race_prizes',0):.2f}"
    )

    kb = [
        [InlineKeyboardButton("💰 Deposit Bonuses", callback_data="ev_depbonus"),
         InlineKeyboardButton("🏆 Deposit Race",    callback_data="ev_deprace")],
        [InlineKeyboardButton("💸 Cashback",        callback_data="ev_cashback")],
        [InlineKeyboardButton("🎁 Giveaways",       callback_data="owner_giveaway"),
         InlineKeyboardButton("🎫 Promo Codes",     callback_data="owner_bonus_codes")],
        [InlineKeyboardButton("🏁 Wager Race",      callback_data="owner_race"),
         InlineKeyboardButton("📋 Commands Help",   callback_data="ev_help")],
        [InlineKeyboardButton("🔄 Refresh",         callback_data="ev_panel"),
         InlineKeyboardButton("« Back",             callback_data="owner_back")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(kb)


def build_depbonus_panel() -> tuple:
    now    = _now()
    bonuses = _ev["deposit_bonuses"]
    lines  = ["💰 <b>DEPOSIT BONUSES</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    btns   = []

    for bid, b in list(bonuses.items()):
        expired = bool(b.get("expires") and now > b["expires"])
        if expired:
            status = "❌ Expired"
        elif b["active"]:
            status = "✅ Active"
        else:
            status = "⏸ Paused"
        exp_str = ""
        if b.get("expires") and not expired:
            exp_str = f" ⏳{_fmt_time_left(b['expires'])}"
        lines.append(
            f"\n<b>{b['title']}</b> [{status}]\n"
            f"  {b['type']} | {b['percentage']:.0f}% | min ${b.get('min_deposit',0):.0f}"
            f" | max ${b.get('max_bonus',0):.0f}{exp_str}\n"
            f"  Claims: {b.get('total_claims',0)} | Paid: ${b.get('total_paid',0):.2f}"
        )
        row = []
        if not expired:
            lbl = "⏸ Pause" if b["active"] else "▶ Resume"
            row.append(InlineKeyboardButton(lbl, callback_data=f"ev_db_toggle_{bid}"))
        row.append(InlineKeyboardButton("🗑 Delete", callback_data=f"ev_db_del_{bid}"))
        btns.append(row)

    if not bonuses:
        lines.append("\nNo deposit bonuses yet.")

    lines.append(
        "\n━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Create via command:</b>\n"
        "<code>/depbonus percentage 50 10 500 48</code>\n"
        "→ 50% on every deposit, min $10, max $500, 48h\n\n"
        "<code>/depbonus weekend 25 5 200</code>\n"
        "→ Sat/Sun only 25% bonus\n\n"
        "<code>/depbonus firstdeposit 100 10 500</code>\n"
        "→ 100% one-time first deposit bonus"
    )
    btns.append([InlineKeyboardButton("🔄 Refresh", callback_data="ev_depbonus"),
                 InlineKeyboardButton("« Back",     callback_data="ev_panel")])
    return "\n".join(lines), InlineKeyboardMarkup(btns)


def build_deprace_panel() -> tuple:
    race = _ev.get("deposit_race", {})
    now  = _now()
    lines = ["🏆 <b>DEPOSIT RACE</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    btns  = []

    if race.get("active") and now <= race.get("end_time", 0):
        lb      = get_deposit_race_leaderboard(10)
        prizes  = race.get("prizes", [])
        medals  = ["🥇", "🥈", "🥉"]
        lines.append(
            f"\n<b>{race.get('title','Deposit Race')}</b> — 🔴 LIVE\n"
            f"⏳ {_fmt_time_left(race['end_time'])} left  |  {len(race.get('deposits',{}))} participants\n"
            f"Total deposited: <b>${race.get('total_deposited',0):.2f}</b>\n"
        )
        for i, p in enumerate(prizes[:3]):
            lines.append(f"  {medals[i]} Prize: <b>${p:,.0f}</b>")
        if lb:
            lines.append("\n<b>Leaderboard:</b>")
            for i, (uid, name, amt) in enumerate(lb):
                med = medals[i] if i < 3 else f"#{i+1}"
                lines.append(f"  {med} {name}: ${amt:.2f}")
        btns = [
            [InlineKeyboardButton("🛑 End Race & Pay Winners", callback_data="ev_deprace_stop")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="ev_deprace"),
             InlineKeyboardButton("« Back",     callback_data="ev_panel")],
        ]
    else:
        if race and not race.get("active"):
            lines.append(f"\nLast race ended: <b>{race.get('title','?')}</b>")
        else:
            lines.append("\nNo deposit race running.")
        lines.append(
            "\n<b>Start a race:</b>\n"
            "<code>/deprace 500 250 100 72 Weekend Deposit Race</code>\n"
            "→ Prizes $500/$250/$100, runs 72 hours\n\n"
            "<code>/stopdeprace</code> — end race early"
        )
        btns = [[InlineKeyboardButton("🔄 Refresh", callback_data="ev_deprace"),
                 InlineKeyboardButton("« Back",     callback_data="ev_panel")]]
    return "\n".join(lines), InlineKeyboardMarkup(btns)


def build_cashback_panel() -> tuple:
    events = _ev["cashback_events"]
    now    = _now()
    lines  = ["💸 <b>CASHBACK EVENTS</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    btns   = []

    for eid, ev in list(events.items()):
        expired = not ev.get("active") or now > ev.get("end_time", 0)
        status  = "❌ Ended" if expired else "✅ Active"
        lines.append(
            f"\n<b>{ev['title']}</b> [{status}]\n"
            f"  {ev['percentage']:.0f}% cashback | Players: {len(ev.get('losses',{}))}\n"
            f"  Total paid: ${ev.get('total_paid',0):.2f}"
        )
        if not expired:
            lines[-1] += f" | ⏳{_fmt_time_left(ev['end_time'])}"
            btns.append([
                InlineKeyboardButton(f"💸 End &amp; Pay ({ev['title'][:12]})", callback_data=f"ev_cb_stop_{eid}"),
                InlineKeyboardButton("🗑", callback_data=f"ev_cb_del_{eid}"),
            ])
        else:
            btns.append([InlineKeyboardButton("🗑 Remove", callback_data=f"ev_cb_del_{eid}")])

    if not events:
        lines.append("\nNo cashback events yet.")

    lines.append(
        "\n━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Create:</b>\n"
        "<code>/cashback 15 24 Weekend Cashback</code>\n"
        "→ 15% of losses returned, 24h period\n\n"
        "<code>/cashback 10 72 VIP Cashback 50 2000</code>\n"
        "→ 10% cashback, min $50 losses, max $2000 per player"
    )
    btns.append([InlineKeyboardButton("🔄 Refresh", callback_data="ev_cashback"),
                 InlineKeyboardButton("« Back",     callback_data="ev_panel")])
    return "\n".join(lines), InlineKeyboardMarkup(btns)


def build_help_panel() -> tuple:
    text = (
        "📋 <b>EVENTS &amp; PROMOS — COMMAND REFERENCE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>All commands are OWNER-ONLY.</i>\n\n"
        "<b>💰 Deposit Bonuses</b>\n"
        "<code>/depbonus percentage PCT MIN MAX HOURS</code>\n"
        "<code>/depbonus weekend PCT MIN MAX</code>\n"
        "<code>/depbonus firstdeposit PCT MIN MAX</code>\n\n"
        "<b>🏆 Deposit Race</b>\n"
        "<code>/deprace P1 P2 P3 HOURS [TITLE]</code>\n"
        "<code>/stopdeprace</code>\n\n"
        "<b>💸 Cashback</b>\n"
        "<code>/cashback PCT HOURS TITLE [MIN_LOSS] [MAX_CB]</code>\n\n"
        "<b>Existing systems:</b>\n"
        "Wager Race: /startrace /stoprace /addracetime\n"
        "Giveaway: /giveaway /smartgiveaway /endgiveaway\n"
        "Promo codes: /createcode /deletecode /codes\n"
        "Personal direct-cash codes: use the bonus-code wizard or add PLAYER_ID and 'direct' to /createcode\n"
    )
    kb = [[InlineKeyboardButton("« Back", callback_data="ev_panel")]]
    return text, InlineKeyboardMarkup(kb)


# ── Callback Handler ──────────────────────────────────────────────────────────
async def handle_ev_callback(query, context, uid: str, data: str, cb: dict):
    """
    Route ev_* callbacks from main.py handle_callback.
    cb = {
        "add_balance":    fn(uid, amount, label),
        "deduct_house":   fn(amount),
        "save":           fn(),            # save_data_critical
        "log_tx":         fn(uid, amount, type_, desc),
        "send_dm":        async fn(uid, text),
        "OWNER_ID":       str,
    }
    Access check is done by caller (uid == str(OWNER_ID)).
    """
    from telegram.error import BadRequest

    async def edit(text, kb=None):
        try:
            await query.edit_message_text(
                text, reply_markup=kb, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
        except BadRequest:
            pass

    async def answer(msg="", alert=False):
        try:
            await query.answer(msg, show_alert=alert)
        except Exception:
            pass

    # ── Main panel ────────────────────────────────────────────────────────────
    if data == "ev_panel":
        await answer()
        text, kb = build_events_panel()
        await edit(text, kb)
        return

    # ── Deposit bonus sub-panel ───────────────────────────────────────────────
    if data == "ev_depbonus":
        await answer()
        text, kb = build_depbonus_panel()
        await edit(text, kb)
        return

    if data.startswith("ev_db_toggle_"):
        bid = data.replace("ev_db_toggle_", "")
        b   = _ev["deposit_bonuses"].get(bid)
        if b:
            b["active"] = not b["active"]
            cb["save"]()
            await answer(f"{'✅ Activated' if b['active'] else '⏸ Paused'}: {b['title']}", alert=True)
        text, kb = build_depbonus_panel()
        await edit(text, kb)
        return

    if data.startswith("ev_db_del_"):
        bid = data.replace("ev_db_del_", "")
        b   = _ev["deposit_bonuses"].pop(bid, None)
        cb["save"]()
        if b:
            await answer(f"🗑 Deleted: {b['title']}", alert=True)
        text, kb = build_depbonus_panel()
        await edit(text, kb)
        return

    # ── Deposit race sub-panel ────────────────────────────────────────────────
    if data == "ev_deprace":
        await answer()
        text, kb = build_deprace_panel()
        await edit(text, kb)
        return

    if data == "ev_deprace_stop":
        results = end_deposit_race()
        cb["save"]()
        medals = ["🥇", "🥈", "🥉"]
        if not results:
            await answer("🏆 Race ended — no eligible winners.", alert=True)
        else:
            msg_parts = ["🏆 <b>Deposit Race Ended!</b>\n"]
            for i, (uid_w, name, deposited, prize) in enumerate(results):
                cb["add_balance"](uid_w, prize, "deposit_race_prize")
                cb["deduct_house"](prize)
                if cb.get("log_tx"):
                    cb["log_tx"](uid_w, prize, "deposit_race_prize", f"Deposit Race: {deposited:.2f} deposited")
                msg_parts.append(f"{medals[i] if i < 3 else f'#{i+1}'} {name}: ${deposited:.2f} deposited → <b>${prize:.0f} prize</b>")
                try:
                    await cb["send_dm"](uid_w,
                        f"🏆 <b>You won the Deposit Race!</b>\n"
                        f"You deposited <b>${deposited:.2f}</b> and earned a <b>${prize:.0f}</b> prize!"
                    )
                except Exception:
                    pass
            cb["save"]()
            await answer(f"✅ {len(results)} winner(s) paid!", alert=True)
        text, kb = build_deprace_panel()
        await edit(text, kb)
        return

    # ── Cashback sub-panel ────────────────────────────────────────────────────
    if data == "ev_cashback":
        await answer()
        text, kb = build_cashback_panel()
        await edit(text, kb)
        return

    if data.startswith("ev_cb_stop_"):
        eid     = data.replace("ev_cb_stop_", "")
        payouts = end_cashback_event(eid)
        cb["save"]()
        paid_count = 0
        for uid_w, amount in payouts:
            cb["add_balance"](uid_w, amount, "cashback")
            cb["deduct_house"](amount)
            if cb.get("log_tx"):
                cb["log_tx"](uid_w, amount, "cashback", "Cashback event payout")
            paid_count += 1
            try:
                await cb["send_dm"](uid_w,
                    f"💸 <b>Cashback Paid!</b>\n"
                    f"You received <b>${amount:.2f}</b> cashback. Good luck!"
                )
            except Exception:
                pass
        cb["save"]()
        await answer(f"💸 Cashback ended: {paid_count} players paid", alert=True)
        text, kb = build_cashback_panel()
        await edit(text, kb)
        return

    if data.startswith("ev_cb_del_"):
        eid = data.replace("ev_cb_del_", "")
        ev  = _ev["cashback_events"].pop(eid, None)
        cb["save"]()
        if ev:
            await answer(f"🗑 Removed: {ev['title']}", alert=True)
        text, kb = build_cashback_panel()
        await edit(text, kb)
        return

    # ── Help ──────────────────────────────────────────────────────────────────
    if data == "ev_help":
        await answer()
        text, kb = build_help_panel()
        await edit(text, kb)
        return
