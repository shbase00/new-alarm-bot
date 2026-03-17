"""
Database - Stores all mint data using SQLite
"""
import sqlite3
import json
import os
from datetime import datetime
from config import DATABASE_PATH

def get_conn():
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create tables if they don't exist"""
    conn = get_conn()
    c = conn.cursor()
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS mints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            chain TEXT DEFAULT 'Unknown',
            mint_link TEXT,
            phases TEXT DEFAULT '[]',
            status TEXT DEFAULT 'upcoming',
            paused INTEGER DEFAULT 0,
            alert_channels TEXT DEFAULT '[]',
            summary_channels TEXT DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            notes TEXT DEFAULT '',
            x_link TEXT DEFAULT '',
            os_link TEXT DEFAULT '',
            contract TEXT DEFAULT '',
            discord_link TEXT DEFAULT '',
            total_supply INTEGER DEFAULT 0,
            minted INTEGER DEFAULT 0,
            market_links TEXT DEFAULT '{}',
            fast_mint_alerted INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sent_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint_id INTEGER,
            phase_name TEXT,
            alert_type TEXT,
            sent_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE,
            channel_name TEXT,
            receive_alerts INTEGER DEFAULT 1,
            receive_summary INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS floor_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint_id INTEGER NOT NULL,
            floor_price REAL NOT NULL,
            recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sweep_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mint_id INTEGER NOT NULL,
            bought_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migrate: add new columns if missing (for existing databases)
    for col_def in [
        "ALTER TABLE mints ADD COLUMN x_link TEXT DEFAULT ''",
        "ALTER TABLE mints ADD COLUMN os_link TEXT DEFAULT ''",
        "ALTER TABLE mints ADD COLUMN contract TEXT DEFAULT ''",
        "ALTER TABLE mints ADD COLUMN discord_link TEXT DEFAULT ''",
        "ALTER TABLE mints ADD COLUMN total_supply INTEGER DEFAULT 0",
        "ALTER TABLE mints ADD COLUMN minted INTEGER DEFAULT 0",
        "ALTER TABLE mints ADD COLUMN market_links TEXT DEFAULT '{}'",
        "ALTER TABLE mints ADD COLUMN fast_mint_alerted INTEGER DEFAULT 0",
    ]:
        try:
            c.execute(col_def)
            conn.commit()
        except Exception:
            pass  # Column already exists

    conn.commit()
    conn.close()

# ── MINT CRUD ──────────────────────────────────────────────

def add_mint(name, chain, mint_link, phases=None, alert_channels=None, summary_channels=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO mints (name, chain, mint_link, phases, alert_channels, summary_channels)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        name, chain, mint_link,
        json.dumps(phases or []),
        json.dumps(alert_channels or []),
        json.dumps(summary_channels or [])
    ))
    mint_id = c.lastrowid
    conn.commit()
    conn.close()
    return mint_id

def get_all_mints():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM mints ORDER BY id DESC").fetchall()
    conn.close()
    return [dict_from_row(r) for r in rows]

def get_mint(mint_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM mints WHERE id=?", (mint_id,)).fetchone()
    conn.close()
    return dict_from_row(row) if row else None

def update_mint(mint_id, **kwargs):
    if not kwargs:
        return
    # Serialize list fields
    for key in ('phases', 'alert_channels', 'summary_channels'):
        if key in kwargs and isinstance(kwargs[key], list):
            kwargs[key] = json.dumps(kwargs[key])
    # market_links is a dict — serialize it too
    if 'market_links' in kwargs and isinstance(kwargs['market_links'], (dict, list)):
        kwargs['market_links'] = json.dumps(kwargs['market_links'])
    
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [mint_id]
    conn = get_conn()
    conn.execute(f"UPDATE mints SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()

def delete_mint(mint_id):
    """Permanently delete a mint and ALL related data from the database."""
    conn = get_conn()
    conn.execute("DELETE FROM mints WHERE id=?", (mint_id,))
    conn.execute("DELETE FROM sent_alerts WHERE mint_id=?", (mint_id,))
    conn.execute("DELETE FROM floor_history WHERE mint_id=?", (mint_id,))
    conn.execute("DELETE FROM sweep_events WHERE mint_id=?", (mint_id,))
    conn.commit()
    conn.close()

def get_todays_mints():
    """Return mints with phases happening today"""
    today = datetime.utcnow().date().isoformat()
    all_mints = get_all_mints()
    todays = []
    for m in all_mints:
        if m['paused']:
            continue
        for phase in m['phases']:
            t = phase.get('time', '')
            if t and t.startswith(today):
                todays.append((m, phase))
                break
    return todays

# ── ALERT TRACKING ──────────────────────────────────────────

def alert_already_sent(mint_id, phase_name, alert_type):
    conn = get_conn()
    row = conn.execute("""
        SELECT id FROM sent_alerts
        WHERE mint_id=? AND phase_name=? AND alert_type=?
    """, (mint_id, phase_name, alert_type)).fetchone()
    conn.close()
    return row is not None

def mark_alert_sent(mint_id, phase_name, alert_type):
    conn = get_conn()
    conn.execute("""
        INSERT INTO sent_alerts (mint_id, phase_name, alert_type)
        VALUES (?, ?, ?)
    """, (mint_id, phase_name, alert_type))
    conn.commit()
    conn.close()

# ── CHANNELS ────────────────────────────────────────────────

def add_channel(channel_id, channel_name="Unknown", alerts=True, summary=True):
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO channels (channel_id, channel_name, receive_alerts, receive_summary)
        VALUES (?, ?, ?, ?)
    """, (str(channel_id), channel_name, int(alerts), int(summary)))
    conn.commit()
    conn.close()

def get_channels():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM channels").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def remove_channel(channel_id):
    conn = get_conn()
    conn.execute("DELETE FROM channels WHERE channel_id=?", (str(channel_id),))
    conn.commit()
    conn.close()

# ── FLOOR HISTORY ────────────────────────────────────────────

def record_floor_price(mint_id: int, floor_price: float):
    conn = get_conn()
    conn.execute(
        "INSERT INTO floor_history (mint_id, floor_price) VALUES (?, ?)",
        (mint_id, floor_price)
    )
    conn.commit()
    conn.close()

def get_last_floor_price(mint_id: int) -> float | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT floor_price FROM floor_history WHERE mint_id=? ORDER BY id DESC LIMIT 1",
        (mint_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None

# ── SWEEP EVENTS ─────────────────────────────────────────────

def record_sweep_event(mint_id: int):
    """Record one NFT purchase event for sweep detection."""
    conn = get_conn()
    conn.execute("INSERT INTO sweep_events (mint_id) VALUES (?)", (mint_id,))
    conn.commit()
    conn.close()

def count_recent_sweeps(mint_id: int, window_seconds: int) -> int:
    """Count purchase events within the last window_seconds for a mint."""
    conn = get_conn()
    row = conn.execute("""
        SELECT COUNT(*) FROM sweep_events
        WHERE mint_id=?
          AND bought_at >= datetime('now', ? || ' seconds')
    """, (mint_id, f"-{window_seconds}")).fetchone()
    conn.close()
    return row[0] if row else 0

def cleanup_old_sweep_events(window_seconds: int = 300):
    """Purge sweep events older than window_seconds to keep the table lean."""
    conn = get_conn()
    conn.execute(
        "DELETE FROM sweep_events WHERE bought_at < datetime('now', ? || ' seconds')",
        (f"-{window_seconds}",)
    )
    conn.commit()
    conn.close()

# ── HELPERS ─────────────────────────────────────────────────

def dict_from_row(row):
    if row is None:
        return None
    d = dict(row)
    for key in ('phases', 'alert_channels', 'summary_channels'):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except Exception:
                d[key] = []
    if 'market_links' in d and isinstance(d['market_links'], str):
        try:
            d['market_links'] = json.loads(d['market_links'])
        except Exception:
            d['market_links'] = {}
    return d
