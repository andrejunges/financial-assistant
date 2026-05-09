import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.environ.get("HISTORY_DB_PATH", "financial_assistant.sqlite3")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                params_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT,
                archived INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_user_created
            ON messages (user_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pending_actions_user_status
            ON pending_actions (user_id, status)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_templates (
                description TEXT PRIMARY KEY,
                category_id INTEGER,
                account_id INTEGER,
                use_count INTEGER NOT NULL DEFAULT 0,
                last_amount_cents INTEGER,
                last_used_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS merchant_aliases (
                alias TEXT PRIMARY KEY,
                canonical_description TEXT NOT NULL,
                use_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_message(user_id: int, role: str, content: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO messages (user_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, role, content, _now()),
        )


def get_recent_messages(user_id: int, limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content
            FROM messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()

    return [
        {"role": row["role"], "content": row["content"]}
        for row in reversed(rows)
    ]


def create_pending_action(user_id: int, action_type: str, params: dict) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO pending_actions (
                user_id, action_type, params_json, status, created_at
            )
            VALUES (?, ?, ?, 'awaiting_confirmation', ?)
            """,
            (user_id, action_type, json.dumps(params), _now()),
        )
        return int(cur.lastrowid)


def get_pending_action(user_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, action_type, params_json, status
            FROM pending_actions
            WHERE user_id = ? AND status = 'awaiting_confirmation'
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

    if row is None:
        return None

    return {
        "id": row["id"],
        "action_type": row["action_type"],
        "params": json.loads(row["params_json"]),
        "status": row["status"],
    }


def resolve_pending_action(action_id: int, status: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE pending_actions
            SET status = ?, resolved_at = ?
            WHERE id = ?
            """,
            (status, _now(), action_id),
        )


def upsert_accounts(accounts: list[dict]) -> None:
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO accounts (id, name, type, archived, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                type = excluded.type,
                archived = excluded.archived,
                updated_at = excluded.updated_at
            """,
            [
                (
                    int(account["id"]),
                    account["name"],
                    account.get("type") or "",
                    1 if account.get("archived") else 0,
                    _now(),
                )
                for account in accounts
            ],
        )


def get_account_name(account_id: int) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT name
            FROM accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()

    if row is None:
        return None

    return row["name"]


def get_account_id_by_name(name: str) -> Optional[int]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM accounts
            WHERE lower(name) = lower(?)
            """,
            (name,),
        ).fetchone()

    if row is None:
        return None

    return int(row["id"])


def upsert_transaction_templates(transactions: list[dict]) -> None:
    with _connect() as conn:
        for tx in transactions:
            description = (tx.get("description") or "").strip()
            if not description:
                continue

            conn.execute(
                """
                INSERT INTO transaction_templates (
                    description,
                    category_id,
                    account_id,
                    use_count,
                    last_amount_cents,
                    last_used_at,
                    updated_at
                )
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(description) DO UPDATE SET
                    category_id = COALESCE(excluded.category_id, transaction_templates.category_id),
                    account_id = COALESCE(excluded.account_id, transaction_templates.account_id),
                    use_count = transaction_templates.use_count + 1,
                    last_amount_cents = excluded.last_amount_cents,
                    last_used_at = excluded.last_used_at,
                    updated_at = excluded.updated_at
                """,
                (
                    description,
                    tx.get("category_id"),
                    tx.get("account_id"),
                    tx.get("amount_cents"),
                    tx.get("date") or _now(),
                    _now(),
                ),
            )


def list_transaction_templates(limit: int = 200) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                description,
                category_id,
                account_id,
                use_count,
                last_amount_cents,
                last_used_at
            FROM transaction_templates
            ORDER BY use_count DESC, last_used_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]


def upsert_alias(alias: str, canonical_description: str) -> None:
    alias = alias.strip().lower()
    canonical_description = canonical_description.strip()
    if not alias or not canonical_description:
        return

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO merchant_aliases (
                alias,
                canonical_description,
                use_count,
                updated_at
            )
            VALUES (?, ?, 1, ?)
            ON CONFLICT(alias) DO UPDATE SET
                canonical_description = excluded.canonical_description,
                use_count = merchant_aliases.use_count + 1,
                updated_at = excluded.updated_at
            """,
            (alias, canonical_description, _now()),
        )


def list_aliases() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT alias, canonical_description, use_count
            FROM merchant_aliases
            ORDER BY use_count DESC, updated_at DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]
