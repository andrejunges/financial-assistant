#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import unicodedata
import warnings
from datetime import date
from difflib import SequenceMatcher
from typing import Optional

def load_local_env() -> None:
    repo_root = os.path.dirname(os.path.abspath(__file__))
    os.environ.setdefault("HISTORY_DB_PATH", os.path.join(repo_root, "financial_assistant.sqlite3"))

    env_path = os.path.join(repo_root, ".env")
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

import organizze_client as org
import storage


def default_account_name() -> str:
    return os.environ.get("DEFAULT_ACCOUNT_NAME", "BTG")


def normalize(text: str) -> str:
    without_accents = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(char)
    )
    return " ".join(without_accents.strip().split())


def parse_quick_entry(text: str) -> tuple[str, Optional[int]]:
    matches = list(re.finditer(r"(?<!\d)(\d+(?:[,.]\d{1,2})?)(?!\d)", text))
    if not matches:
        return text.strip(), None

    match = matches[-1]
    amount_text = match.group(1).replace(",", ".")
    amount_cents = int(round(float(amount_text) * 100))
    description = f"{text[:match.start()]} {text[match.end():]}".strip()
    description = re.sub(r"\s+", " ", description)
    return description, amount_cents


def fetch_and_cache_accounts() -> list[dict]:
    accounts = org.get_accounts()
    storage.upsert_accounts(accounts)
    return accounts


def list_accounts() -> list[dict]:
    storage.init_db()
    accounts = fetch_and_cache_accounts()
    default_id = resolve_account_id()
    return [
        {
            "id": int(account["id"]),
            "name": account["name"],
            "default": default_id is not None and int(account["id"]) == int(default_id),
        }
        for account in accounts
    ]


def list_funding_sources() -> list[dict]:
    storage.init_db()
    accounts = fetch_and_cache_accounts()
    credit_cards = org.get_credit_cards()
    default_name = normalize(default_account_name())

    sources = [
        {
            "id": int(account["id"]),
            "kind": "account",
            "value": f"account:{int(account['id'])}",
            "name": account["name"],
            "default": False,
        }
        for account in accounts
        if not account.get("archived")
    ]
    sources.extend(
        {
            "id": int(card["id"]),
            "kind": "credit_card",
            "value": f"credit_card:{int(card['id'])}",
            "name": card["name"],
            "default": False,
        }
        for card in credit_cards
        if not card.get("archived")
    )

    default_source = next(
        (
            source for source in sources
            if source["kind"] == "credit_card" and default_name in normalize(source["name"])
        ),
        None,
    )
    if default_source is None:
        default_source = next(
            (
                source for source in sources
                if source["kind"] == "credit_card" and "btg" in normalize(source["name"])
            ),
            None,
        )
    if default_source is None:
        default_source = next(
            (
                source for source in sources
                if default_name in normalize(source["name"])
            ),
            None,
        )

    if default_source is None and sources:
        default_source = sources[0]

    if default_source is not None:
        default_source["default"] = True

    return sources


def list_tags(days: int, include_credit_cards: bool) -> list[dict]:
    return org.get_tags(days=days, include_credit_cards=include_credit_cards)


def resolve_account_id(name: Optional[str] = None) -> Optional[int]:
    name = name or default_account_name()
    cached_id = storage.get_account_id_by_name(name)
    if cached_id:
        return cached_id

    accounts = fetch_and_cache_accounts()
    normalized = normalize(name)
    for account in accounts:
        if normalize(account["name"]) == normalized:
            return int(account["id"])
    return None


def account_name(account_id: Optional[int], fetch_missing: bool = True) -> str:
    if account_id is None:
        return ""

    cached = storage.get_account_name(int(account_id))
    if cached:
        return cached

    if not fetch_missing:
        return ""

    fetch_and_cache_accounts()
    return storage.get_account_name(int(account_id)) or ""


def refresh_templates(days: int) -> int:
    storage.init_db()
    fetch_and_cache_accounts()
    transactions = org.get_transactions(days=days)
    storage.upsert_transaction_templates(transactions)
    return len(transactions)


def template_score(query: str, template: dict, aliases: list[dict]) -> float:
    normalized_query = normalize(query)
    normalized_description = normalize(template["description"])
    score = SequenceMatcher(None, normalized_query, normalized_description).ratio()

    if normalized_query and normalized_query in normalized_description:
        score += 0.35

    for alias in aliases:
        if alias["canonical_description"] != template["description"]:
            continue
        normalized_alias = normalize(alias["alias"])
        alias_score = SequenceMatcher(None, normalized_query, normalized_alias).ratio()
        if normalized_query and normalized_query in normalized_alias:
            alias_score += 0.35
        score = max(score, alias_score + min(alias["use_count"], 10) * 0.01)

    score += min(template.get("use_count") or 0, 20) * 0.01
    return score


def suggest(text: str, limit: int, refresh_if_empty: bool) -> list[dict]:
    storage.init_db()
    description_query, amount_cents = parse_quick_entry(text)
    if amount_cents is None:
        amount_cents = 0

    templates = storage.list_transaction_templates()
    if refresh_if_empty and not templates:
        refresh_templates(days=180)
        templates = storage.list_transaction_templates()

    aliases = storage.list_aliases()
    default_account_id = resolve_account_id()

    ranked = sorted(
        templates,
        key=lambda template: template_score(description_query, template, aliases),
        reverse=True,
    )

    suggestions = []
    for template in ranked[:limit]:
        account_id = template.get("account_id") or default_account_id
        if account_id and not account_name(account_id, fetch_missing=False):
            account_id = default_account_id
        suggestions.append(
            {
                "description": template["description"],
                "input_description": description_query,
                "amount_cents": -abs(amount_cents),
                "date": date.today().isoformat(),
                "account_id": account_id,
                "account_name": account_name(account_id),
                "category_id": template.get("category_id"),
                "score": round(template_score(description_query, template, aliases), 4),
            }
        )

    if description_query:
        suggestions.append(
            {
                "description": description_query,
                "input_description": description_query,
                "amount_cents": -abs(amount_cents),
                "date": date.today().isoformat(),
                "account_id": default_account_id,
                "account_name": account_name(default_account_id),
                "category_id": None,
                "score": 0,
            }
        )

    return suggestions[:limit]


def create_from_payload(payload: dict) -> dict:
    storage.init_db()
    description = payload["description"].strip()
    input_description = payload.get("input_description", "").strip()
    amount_cents = -abs(int(payload["amount_cents"]))
    account_id = int(payload["account_id"])
    category_id = payload.get("category_id")

    result = org.create_transaction(
        description=description,
        amount_cents=amount_cents,
        date=payload.get("date") or date.today().isoformat(),
        account_id=account_id,
        category_id=int(category_id) if category_id else None,
        notes=payload.get("notes"),
        tags=payload.get("tags"),
        credit_card_id=int(payload["credit_card_id"]) if payload.get("credit_card_id") else None,
    )

    tx_template = {
        "description": description,
        "amount_cents": amount_cents,
        "date": payload.get("date") or date.today().isoformat(),
        "account_id": account_id,
        "category_id": int(category_id) if category_id else None,
    }
    storage.upsert_transaction_templates([tx_template])
    if input_description and normalize(input_description) != normalize(description):
        storage.upsert_alias(input_description, description)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Local finance assistant CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    suggest_parser = subparsers.add_parser("suggest")
    suggest_parser.add_argument("text")
    suggest_parser.add_argument("--limit", type=int, default=5)
    suggest_parser.add_argument("--no-refresh", action="store_true")

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--payload", required=True)

    subparsers.add_parser("accounts")
    subparsers.add_parser("funding-sources")

    tags_parser = subparsers.add_parser("tags")
    tags_parser.add_argument("--days", type=int, default=365)
    tags_parser.add_argument("--no-credit-cards", action="store_true")

    refresh_parser = subparsers.add_parser("refresh-templates")
    refresh_parser.add_argument("--days", type=int, default=180)

    args = parser.parse_args()

    if args.command == "suggest":
        print(json.dumps(suggest(args.text, args.limit, not args.no_refresh), ensure_ascii=False))
        return 0

    if args.command == "create":
        payload = json.loads(args.payload)
        print(json.dumps(create_from_payload(payload), ensure_ascii=False))
        return 0

    if args.command == "accounts":
        print(json.dumps(list_accounts(), ensure_ascii=False))
        return 0

    if args.command == "funding-sources":
        print(json.dumps(list_funding_sources(), ensure_ascii=False))
        return 0

    if args.command == "tags":
        print(json.dumps(list_tags(args.days, not args.no_credit_cards), ensure_ascii=False))
        return 0

    if args.command == "refresh-templates":
        count = refresh_templates(args.days)
        print(json.dumps({"transactions_cached": count}, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
