import aiosqlite
from datetime import datetime, timezone

DB_PATH = "prediction_markets.db"

def utcnow():
    return datetime.now(timezone.utc).isoformat()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            rules TEXT DEFAULT '',
            source TEXT DEFAULT '',
            close_time_utc TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',      -- OPEN/CLOSED/RESOLVED_PENDING/RESOLVED/VOID
            pending_outcome TEXT,                    -- YES/NO
            resolved_outcome TEXT,                   -- YES/NO
            resolved_pending_at TEXT,                -- UTC ISO datetime
            yes_pool REAL NOT NULL DEFAULT 50.0,
            no_pool REAL NOT NULL DEFAULT 50.0,
            created_at TEXT NOT NULL
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            user_id INTEGER NOT NULL,
            market_id INTEGER NOT NULL,
            yes_shares REAL NOT NULL DEFAULT 0.0,
            no_shares REAL NOT NULL DEFAULT 0.0,
            PRIMARY KEY(user_id, market_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            market_id INTEGER NOT NULL,
            side TEXT NOT NULL,
            amount_usdt REAL NOT NULL,
            shares REAL NOT NULL,
            price REAL NOT NULL,
            fee_usdt REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_ui (
            user_id INTEGER PRIMARY KEY,
            last_slip_msg_id INTEGER
        )
        """)

        await db.commit()

async def create_market(category: str, title: str, close_time_utc: str, 
                        yes_pool: float, no_pool: float,
                        rules: str = "", source: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO markets(category,title,rules,source,close_time_utc,status,resolved_outcome,yes_pool,no_pool,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (category, title, rules, source, close_time_utc, "OPEN", None, yes_pool, no_pool, utcnow()))
        await db.commit()

async def get_market(market_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT id,category,title,close_time_utc,status,resolved_outcome,yes_pool,no_pool,rules,source,pending_outcome,resolved_pending_at
            FROM markets WHERE id=?
        """, (market_id,))
        return await cur.fetchone()

async def fetch_markets(category: str | None = None, status: str | None = None, limit: int = 30):
    q = """SELECT id,category,title,close_time_utc,status,resolved_outcome,yes_pool,no_pool,rules,source,pending_outcome,resolved_pending_at FROM markets"""
    params = []
    where = []
    if category:
        where.append("category=?")
        params.append(category)
    if status:
        where.append("status=?")
        params.append(status)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(q, params)
        return await cur.fetchall()

async def update_market_status(market_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE markets SET status=? WHERE id=?", (status, market_id))
        await db.commit()

async def delete_market(market_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM markets WHERE id=?", (market_id,))
        await db.execute("DELETE FROM positions WHERE market_id=?", (market_id,))
        await db.execute("DELETE FROM trades WHERE market_id=?", (market_id,))
        await db.commit()

async def resolve_market(market_id: int, outcome: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE markets SET status='RESOLVED', resolved_outcome=? WHERE id=?
        """, (outcome, market_id))
        await db.commit()

async def void_market(market_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE markets SET status='VOID' WHERE id=?", (market_id,))
        await db.commit()

async def add_trade(user_id: int, market_id: int, side: str, amount: float, shares: float, price: float, fee: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO trades(user_id,market_id,side,amount_usdt,shares,price,fee_usdt,created_at)
            VALUES(?,?,?,?,?,?,?,?)
        """, (user_id, market_id, side, amount, shares, price, fee, utcnow()))
        await db.commit()

async def update_pool(market_id: int, side: str, amount_to_pool: float):
    async with aiosqlite.connect(DB_PATH) as db:
        if side == "YES":
            await db.execute("UPDATE markets SET yes_pool = yes_pool + ? WHERE id=?", (amount_to_pool, market_id))
        else:
            await db.execute("UPDATE markets SET no_pool = no_pool + ? WHERE id=?", (amount_to_pool, market_id))
        await db.commit()

async def upsert_position(user_id: int, market_id: int, side: str, shares: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO positions(user_id,market_id,yes_shares,no_shares)
            VALUES(?,?,0,0)
            ON CONFLICT(user_id, market_id) DO NOTHING
        """, (user_id, market_id))

        if side == "YES":
            await db.execute("""
                UPDATE positions SET yes_shares = yes_shares + ?
                WHERE user_id=? AND market_id=?
            """, (shares, user_id, market_id))
        else:
            await db.execute("""
                UPDATE positions SET no_shares = no_shares + ?
                WHERE user_id=? AND market_id=?
            """, (shares, user_id, market_id))
        await db.commit()

async def get_position(user_id: int, market_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT yes_shares,no_shares FROM positions
            WHERE user_id=? AND market_id=?
        """, (user_id, market_id))
        row = await cur.fetchone()
    return row or (0.0, 0.0)

async def subtract_position(user_id: int, market_id: int, side: str, shares: float):
    async with aiosqlite.connect(DB_PATH) as db:
        if side == "YES":
            await db.execute("""
                UPDATE positions SET yes_shares = yes_shares - ?
                WHERE user_id=? AND market_id=?
            """, (shares, user_id, market_id))
        else:
            await db.execute("""
                UPDATE positions SET no_shares = no_shares - ?
                WHERE user_id=? AND market_id=?
            """, (shares, user_id, market_id))
        await db.commit()

async def fetch_positions_in_market(market_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT user_id, yes_shares, no_shares FROM positions WHERE market_id=?
        """, (market_id,))
        return await cur.fetchall()

async def fetch_user_spend_in_market(market_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT user_id, SUM(amount_usdt)
            FROM trades WHERE market_id=?
            GROUP BY user_id
        """, (market_id,))
        return await cur.fetchall()

async def user_total_spend_in_market(user_id: int, market_id: int) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT COALESCE(SUM(amount_usdt),0)
            FROM trades
            WHERE user_id=? AND market_id=?
        """, (user_id, market_id))
        row = await cur.fetchone()
    return float(row[0] or 0.0)

async def add_liquidity(market_id: int, yes_add: float, no_add: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE markets
            SET yes_pool = yes_pool + ?, no_pool = no_pool + ?
            WHERE id=?
        """, (yes_add, no_add, market_id))
        await db.commit()

async def close_market_now(market_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE markets SET status='CLOSED' WHERE id=?", (market_id,))
        await db.commit()

async def set_resolve_pending(market_id: int, outcome: str, ts_utc: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE markets
            SET status='RESOLVED_PENDING',
                pending_outcome=?,
                resolved_pending_at=?
            WHERE id=?
        """, (outcome, ts_utc, market_id))
        await db.commit()

async def finalize_resolution(market_id: int, outcome: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE markets
            SET status='RESOLVED',
                resolved_outcome=?,
                pending_outcome=NULL,
                resolved_pending_at=NULL
            WHERE id=?
        """, (outcome, market_id))
        await db.commit()

async def cancel_pending_resolution(market_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE markets
            SET status='CLOSED',
                pending_outcome=NULL,
                resolved_pending_at=NULL
            WHERE id=?
        """, (market_id,))
        await db.commit()

async def set_last_slip(user_id: int, msg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO user_ui(user_id,last_slip_msg_id) VALUES(?,?)
        ON CONFLICT(user_id) DO UPDATE SET last_slip_msg_id=excluded.last_slip_msg_id
        """, (user_id, msg_id))
        await db.commit()

async def get_last_slip(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT last_slip_msg_id FROM user_ui WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
    return row[0] if row else None
