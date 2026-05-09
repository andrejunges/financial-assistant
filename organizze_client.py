"""
Organizze REST API client.
Docs: https://github.com/organizze/api-doc
Base URL: https://api.organizze.com.br/rest/v2
Auth: HTTP Basic (email:api_token)
"""

import os
import requests
import time
import unicodedata
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

def _normalize_text(text: str) -> str:
    without_accents = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(char)
    )
    return " ".join(without_accents.strip().split())

def _normalize_tags(tags) -> list[dict]:
    if not tags:
        return []

    if isinstance(tags, str):
        tags = [piece.strip() for piece in tags.split(",")]

    normalized = []
    for tag in tags:
        if isinstance(tag, str):
            name = tag.strip()
        elif isinstance(tag, dict):
            name = str(tag.get("name") or "").strip()
        else:
            name = ""

        if name:
            normalized.append({"name": name})

    return normalized

def _tag_names(tags) -> list[str]:
    return [tag["name"] for tag in _normalize_tags(tags)]

def _collect_tags(
    summary: dict,
    tags,
    *,
    date_text: Optional[str],
    source: str,
    transaction_id: Optional[int],
) -> None:
    for name in _tag_names(tags):
        normalized = _normalize_text(name)
        entry = summary.setdefault(
            normalized,
            {
                "name": name,
                "normalized_name": normalized,
                "use_count": 0,
                "last_used_at": None,
                "sources": [],
            },
        )
        entry["use_count"] += 1
        if date_text and (entry["last_used_at"] is None or date_text > entry["last_used_at"]):
            entry["last_used_at"] = date_text
        if len(entry["sources"]) < 5:
            entry["sources"].append(
                {
                    "source": source,
                    "transaction_id": transaction_id,
                    "date": date_text,
                }
            )

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

# ── Credit cards ─────────────────────────────────────────────────────────────

def get_credit_cards() -> list:
    """Return all credit cards."""
    cards = _cached("credit_cards", lambda: _get("/credit_cards"))
    return [
        {
            "id": c["id"],
            "name": c["name"],
            "description": c.get("description") or "",
            "card_network": c.get("card_network") or "",
            "closing_day": c.get("closing_day"),
            "due_day": c.get("due_day"),
            "limit_brl": (c.get("limit_cents") or 0) / 100,
            "limit_cents": c.get("limit_cents") or 0,
            "archived": c.get("archived", False),
            "default": c.get("default", False),
            "type": c.get("type") or c.get("kind", "credit_card"),
        }
        for c in cards
    ]

def _resolve_credit_card_id(
    credit_card_id: Optional[int] = None,
    credit_card_name: Optional[str] = None,
) -> Optional[int]:
    if credit_card_id:
        return int(credit_card_id)

    if not credit_card_name:
        return None

    normalized = _normalize_text(credit_card_name)
    for card in get_credit_cards():
        card_name = _normalize_text(card["name"])
        if card_name == normalized or normalized in card_name or card_name in normalized:
            return int(card["id"])

    return None

def get_credit_card_invoices(
    credit_card_id: Optional[int] = None,
    credit_card_name: Optional[str] = None,
) -> list:
    """Return invoices for a credit card."""
    resolved_id = _resolve_credit_card_id(credit_card_id, credit_card_name)
    if resolved_id is None:
        raise ValueError("credit_card_id or credit_card_name is required")

    invoices = _get(f"/credit_cards/{resolved_id}/invoices")
    return [
        {
            "id": i["id"],
            "date": i["date"],
            "starting_date": i["starting_date"],
            "closing_date": i["closing_date"],
            "amount_brl": i.get("amount_cents", 0) / 100,
            "amount_cents": i.get("amount_cents", 0),
            "payment_amount_brl": i.get("payment_amount_cents", 0) / 100,
            "payment_amount_cents": i.get("payment_amount_cents", 0),
            "balance_brl": i.get("balance_cents", 0) / 100,
            "balance_cents": i.get("balance_cents", 0),
            "previous_balance_brl": i.get("previous_balance_cents", 0) / 100,
            "previous_balance_cents": i.get("previous_balance_cents", 0),
            "credit_card_id": i.get("credit_card_id"),
        }
        for i in invoices
    ]

def _format_invoice_transaction(t: dict, categories_by_id: dict) -> dict:
    return {
        "id": t["id"],
        "description": t["description"],
        "amount_brl": t["amount_cents"] / 100,
        "amount_cents": t["amount_cents"],
        "date": t["date"],
        "category_id": t.get("category_id"),
        "category": categories_by_id.get(t.get("category_id"), ""),
        "credit_card_id": t.get("credit_card_id"),
        "credit_card_invoice_id": t.get("credit_card_invoice_id"),
        "installment": t.get("installment"),
        "total_installments": t.get("total_installments"),
        "notes": t.get("notes") or "",
        "tags": t.get("tags", []),
        "paid": t.get("paid", False),
    }

def get_credit_card_invoice(
    credit_card_id: Optional[int] = None,
    invoice_id: Optional[int] = None,
    credit_card_name: Optional[str] = None,
) -> dict:
    """Return one credit-card invoice with its transactions."""
    resolved_id = _resolve_credit_card_id(credit_card_id, credit_card_name)
    if resolved_id is None:
        raise ValueError("credit_card_id or credit_card_name is required")
    if not invoice_id:
        raise ValueError("invoice_id is required")

    invoice = _get(f"/credit_cards/{resolved_id}/invoices/{int(invoice_id)}")
    categories_by_id = {c["id"]: c["name"] for c in get_categories()}
    transactions = [
        _format_invoice_transaction(t, categories_by_id)
        for t in invoice.get("transactions", [])
    ]
    return {
        "id": invoice["id"],
        "date": invoice["date"],
        "starting_date": invoice["starting_date"],
        "closing_date": invoice["closing_date"],
        "amount_brl": invoice.get("amount_cents", 0) / 100,
        "amount_cents": invoice.get("amount_cents", 0),
        "payment_amount_brl": invoice.get("payment_amount_cents", 0) / 100,
        "payment_amount_cents": invoice.get("payment_amount_cents", 0),
        "balance_brl": invoice.get("balance_cents", 0) / 100,
        "balance_cents": invoice.get("balance_cents", 0),
        "previous_balance_brl": invoice.get("previous_balance_cents", 0) / 100,
        "previous_balance_cents": invoice.get("previous_balance_cents", 0),
        "credit_card_id": invoice.get("credit_card_id"),
        "transactions": transactions,
    }

def get_credit_card_monthly_expense(
    credit_card_id: Optional[int] = None,
    credit_card_name: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    include_transactions: bool = True,
) -> dict:
    """Return the invoice spend for a credit card month.

    When year/month are omitted, this selects the invoice period containing today.
    If no invoice period contains today, it falls back to the next invoice due date.
    """
    today = date.today()
    target_year = int(year) if year else today.year
    target_month = int(month) if month else today.month
    invoices = get_credit_card_invoices(credit_card_id, credit_card_name)

    selected = None
    if year or month:
        for invoice in invoices:
            invoice_date = date.fromisoformat(invoice["date"])
            if invoice_date.year == target_year and invoice_date.month == target_month:
                selected = invoice
                break
    else:
        for invoice in invoices:
            starts = date.fromisoformat(invoice["starting_date"])
            closes = date.fromisoformat(invoice["closing_date"])
            if starts <= today <= closes:
                selected = invoice
                break
        if selected is None:
            future_invoices = [
                invoice for invoice in invoices
                if date.fromisoformat(invoice["date"]) >= today
            ]
            selected = future_invoices[0] if future_invoices else invoices[-1]

    if selected is None:
        raise ValueError(f"No invoice found for {target_year}-{target_month:02d}")

    if not include_transactions:
        return selected

    return get_credit_card_invoice(
        credit_card_id=selected["credit_card_id"],
        invoice_id=selected["id"],
    )

def get_tags(days: int = 365, include_credit_cards: bool = True) -> list:
    """Return tags observed in recent transactions and credit-card invoices.

    Organizze does not expose a standalone tags endpoint in the public v2 API,
    so this derives the existing tag list from transactions that already use tags.
    """
    start = date.today() - timedelta(days=days)
    summary = {}

    for tx in get_transactions(days=days):
        _collect_tags(
            summary,
            tx.get("tags"),
            date_text=tx.get("date"),
            source="transaction",
            transaction_id=tx.get("id"),
        )

    if include_credit_cards:
        for card in get_credit_cards():
            if card.get("archived"):
                continue
            for invoice in get_credit_card_invoices(credit_card_id=int(card["id"])):
                closes = date.fromisoformat(invoice["closing_date"])
                starts = date.fromisoformat(invoice["starting_date"])
                if closes < start or starts > date.today():
                    continue
                invoice_detail = get_credit_card_invoice(
                    credit_card_id=int(card["id"]),
                    invoice_id=int(invoice["id"]),
                )
                for tx in invoice_detail.get("transactions", []):
                    _collect_tags(
                        summary,
                        tx.get("tags"),
                        date_text=tx.get("date"),
                        source=f"credit_card:{card['name']}",
                        transaction_id=tx.get("id"),
                    )

    return sorted(
        summary.values(),
        key=lambda tag: (-tag["use_count"], tag["name"].lower()),
    )

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
            "tags": t.get("tags", []),
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
    tags: Optional[list] = None,
    credit_card_id: Optional[int] = None,
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
    if tags:
        payload["tags"] = _normalize_tags(tags)
    if credit_card_id:
        payload["credit_card_id"] = credit_card_id

    result = _post("/transactions", payload)
    return {
        "id": result["id"],
        "description": result["description"],
        "amount_brl": result["amount_cents"] / 100,
        "date": result["date"],
        "account": result.get("account_name", ""),
        "account_id": result.get("account_id"),
        "account_type": result.get("account_type"),
        "category": result.get("category_name", ""),
        "credit_card_id": result.get("credit_card_id"),
        "tags": result.get("tags", []),
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
