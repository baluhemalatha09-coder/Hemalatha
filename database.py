"""SQLite persistence: users and recommendation history (stdlib only)."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent


def get_db_path() -> Path:
    path = Path(os.getenv("DATABASE_PATH", "data/pocketsmart.db"))
    return path if path.is_absolute() else BASE_DIR / path


@contextmanager
def get_conn():
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                email TEXT,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                budget REAL NOT NULL,
                inputs TEXT NOT NULL,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id DESC);
            """
        )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------- users ------------------------------------- #
def create_user(username: str, email: str, password_hash: str) -> int:
    """Insert a user. Raises sqlite3.IntegrityError if the username exists."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (username, email, password_hash, _now()),
        )
        return cur.lastrowid


def get_user_by_username(username: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


# ----------------------------- history ----------------------------------- #
def _history_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["inputs"] = json.loads(d["inputs"])
    d["result"] = json.loads(d["result"])
    return d


def add_history(user_id: int, category: str, budget: float, inputs: dict, result: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO history (user_id, category, budget, inputs, result, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, category, budget, json.dumps(inputs), json.dumps(result), _now()),
        )
        return cur.lastrowid


def list_history(user_id: int, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()
        return [_history_row(r) for r in rows]


def get_history_item(user_id: int, item_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM history WHERE id = ? AND user_id = ?", (item_id, user_id)
        ).fetchone()
        return _history_row(row) if row else None


def history_stats(user_id: int) -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n, COALESCE(SUM(budget), 0) AS total "
            "FROM history WHERE user_id = ? GROUP BY category",
            (user_id,),
        ).fetchall()
    by_cat = {r["category"]: r["n"] for r in rows}
    return {
        "total_plans": sum(by_cat.values()),
        "total_budget": round(sum(r["total"] for r in rows)),
        "by_category": by_cat,
    }
