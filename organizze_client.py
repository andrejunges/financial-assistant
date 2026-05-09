"""
Organizze REST API client.
Docs: https://github.com/organizze/api-doc
Base URL: https://api.organizze.com.br/rest/v2
Auth: HTTP Basic (email:api_token)
"""

import os
import requests
import time
from datetime import date, timedelta
from typing import Optional

BASE_URL = "https://api.organizze.com.br/rest/v2"
REFERENCE_CACHE_TTL_SECONDS = int(os.environ.get("REFERENCE_CACHE_TTL_SECONDS", "300"))
_cache = {}

def _session() -> requests.Session:
    s = requests.Session()
    s.auth = (
        os.environ["ORGANIZZE_EMAIL"],
        os.environ["ORGANIZZE_API_TOKEN"],
    )
    s.headers.update({
        "Content-Type": "application/json",
        "User-Agent": "TelegramBot/1.0",
    })
    return s

def _get(path: str, params: dict = None):
    r = _session().get(f"{BASE_URL}{path}", params=params)
    r.raise_for_status()
    return r.json()

def _post(path: str, data: dict):
    r = _session().post(f"{BASE_URL}{path}", json=data)
    r.raise_for_status()
    return r.json()

def _delete(path: str, data: dict = None):
    r = _session().delete(f"{BASE_URL}{path}", json=data or {})
    r.raise_for_status()
    return r.json()

def _cached(key: str, fetcher):
    cached = _cache.get(key)
    now = time.time()
    if cached and now - cached["created_at"] < REFERENCE_CACHE_TTL_SECONDS:
        return cached["value"]

    value = fetcher()
    _cache[key] = {"created_at": now, "value": value}
    return value

# ── Accounts ────────────────────────────────────────────────────────────────

def get_accounts() -> list:
    """Return all bank accounts.

    Organizze's /accounts endpoint does not include current balance fields.
    """
    accounts = _cached("accounts", lambda: _get("/accounts"))
    return [
        {
            "id": a["id"],
            "name": a["name"],
            "description": a.get("description") or "",
            "type": a.get("type", ""),
            "archived": a.get("archived", False),
            "default": a.get("default", False),
        }
        for a in accounts
    ]

# ── Transactions ─────────────────────────────────────────────────────────────

def get_transactions(days: int = 30, account_id: Optional[int] = None) -> list:
    """Return transactions for the last `days` days."""
    start = (date.today() - timedelta(days=days)).isoformat()
    end = date.today().isoformat()
    params = {"start_date": start, "end_date": end}
    if account_id:
        params["account_id"] = account_id

    txs = _get("/transactions", params=params)
    accounts_by_id = {a["id"]: a["name"] for a in get_accounts()}
    categories_by_id = {c["id"]: c["name"] for c in get_categories()}
    return [
        {
            "id": t["id"],
            "description": t["description"],
            "amount_brl": t["amount_cents"] / 100,
            "amount_cents": t["amount_cents"],
            "date": t["date"],
            "category_id": t.get("category_id"),
            "category": categories_by_id.get(t.get("category_id"), ""),
            "account_id": t.get("account_id"),
            "account": accounts_by_id.get(t.get("account_id"), ""),
            "notes": t.get("notes", ""),
            "paid": t.get("paid", True),
        }
        for t in txs
    ]

def create_transaction(
    description: str,
    amount_cents: int,
    date: str,
    account_id: int,
    category_id: Optional[int] = None,
    notes: Optional[str] = None,
    paid: bool = True,
) -> dict:
    """
    Create a transaction.
    amount_cents: negative = expense, positive = income.
    date: YYYY-MM-DD string.
    """
    payload = {
        "description": description,
        "date": date,
        "amount_cents": amount_cents,
        "account_id": account_id,
        "paid": paid,
    }
    if category_id:
        payload["category_id"] = category_id
    if notes:
        payload["notes"] = notes

    result = _post("/transactions", payload)
    return {
        "id": result["id"],
        "description": result["description"],
        "amount_brl": result["amount_cents"] / 100,
        "date": result["date"],
        "account": result.get("account_name", ""),
        "category": result.get("category_name", ""),
    }

def delete_transaction(
    transaction_id: int,
    update_future: bool = False,
    update_all: bool = False,
) -> dict:
    """Delete a transaction by id."""
    payload = {}
    if update_future:
        payload["update_future"] = True
    if update_all:
        payload["update_all"] = True

    result = _delete(f"/transactions/{transaction_id}", payload)
    return {
        "id": result["id"],
        "description": result["description"],
        "amount_brl": result["amount_cents"] / 100,
        "amount_cents": result["amount_cents"],
        "date": result["date"],
        "account_id": result.get("account_id"),
        "category_id": result.get("category_id"),
    }

# ── Categories ───────────────────────────────────────────────────────────────

def get_categories() -> list:
    cats = _cached("categories", lambda: _get("/categories"))
    return [
        {
            "id": c["id"],
            "name": c["name"],
            "type": c.get("kind", ""),  # 'expense' or 'income'
            "color": c.get("color", ""),
        }
        for c in cats
    ]

# ── Budgets ──────────────────────────────────────────────────────────────────

def get_budgets() -> list:
    """Return this month's budgets with planned vs actual."""
    today = date.today()
    budgets = _get(f"/budgets/{today.year}/{today.month:02d}")
    categories_by_id = {c["id"]: c["name"] for c in get_categories()}
    return [
        {
            "category_id": b.get("category_id"),
            "category": categories_by_id.get(b.get("category_id"), ""),
            "planned_brl": b.get("amount_in_cents", 0) / 100,
            "actual_brl": b.get("total", 0) / 100,
            "predicted_brl": b.get("predicted_total", 0) / 100,
            "remaining_brl": (b.get("amount_in_cents", 0) - b.get("total", 0)) / 100,
            "percentage": b.get("percentage"),
        }
        for b in budgets
        if b.get("amount_in_cents", 0) != 0
    ]
