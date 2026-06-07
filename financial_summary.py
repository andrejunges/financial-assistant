from collections import defaultdict
from datetime import date, timedelta

import organizze_client as org


def _format_brl(cents: int) -> str:
    amount = abs(cents) / 100
    return f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _amount_cents(transaction: dict) -> int:
    if transaction.get("amount_cents") is not None:
        return int(transaction["amount_cents"])
    return int(round(float(transaction.get("amount_brl") or 0) * 100))


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _period(days: int, today: date | None = None) -> tuple[date, date]:
    end = today or date.today()
    start = end - timedelta(days=max(days, 1) - 1)
    return start, end


def _within_period(transaction: dict, start: date, end: date) -> bool:
    transaction_date = _parse_date(transaction["date"])
    return start <= transaction_date <= end


def _category_name(transaction: dict) -> str:
    return transaction.get("category") or "Sem categoria"


def _source_name(transaction: dict, fallback: str) -> str:
    return transaction.get("account") or transaction.get("credit_card") or fallback


def _top_items(transactions: list[dict], limit: int = 5) -> list[dict]:
    return sorted(
        transactions,
        key=lambda tx: abs(_amount_cents(tx)),
        reverse=True,
    )[:limit]


def _summarize_categories(transactions: list[dict]) -> list[tuple[str, int]]:
    totals = defaultdict(int)
    for tx in transactions:
        totals[_category_name(tx)] += abs(_amount_cents(tx))
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


def _fetch_account_transactions(days: int, start: date, end: date) -> list[dict]:
    transactions = org.get_transactions(days=days + 1)
    return [tx for tx in transactions if _within_period(tx, start, end)]


def _fetch_credit_card_transactions(start: date, end: date) -> list[dict]:
    transactions = []
    seen = set()

    for card in org.get_credit_cards():
        if card.get("archived"):
            continue

        for invoice in org.get_credit_card_invoices(credit_card_id=int(card["id"])):
            invoice_start = _parse_date(invoice["starting_date"])
            invoice_end = _parse_date(invoice["closing_date"])
            if invoice_end < start or invoice_start > end:
                continue

            invoice_detail = org.get_credit_card_invoice(
                credit_card_id=int(card["id"]),
                invoice_id=int(invoice["id"]),
            )
            for tx in invoice_detail.get("transactions", []):
                if not _within_period(tx, start, end):
                    continue
                key = (int(card["id"]), int(tx["id"]))
                if key in seen:
                    continue
                seen.add(key)
                transactions.append({**tx, "credit_card": card["name"]})

    return transactions


def _budget_lines(limit: int = 3) -> list[str]:
    try:
        budgets = org.get_budgets()
    except Exception:
        return []

    watched = []
    for budget in budgets:
        planned = int(round(float(budget.get("planned_brl") or 0) * 100))
        actual = int(round(float(budget.get("actual_brl") or 0) * 100))
        if planned <= 0:
            continue

        percentage = budget.get("percentage")
        if percentage is None:
            percentage = round((actual / planned) * 100)
        watched.append((float(percentage), budget["category"], actual, planned))

    lines = []
    for percentage, category, actual, planned in sorted(watched, reverse=True)[:limit]:
        lines.append(
            f"- {category}: {_format_brl(actual)} de {_format_brl(planned)} ({percentage:.0f}%)"
        )
    return lines


def build_period_summary(days: int = 7, today: date | None = None) -> str:
    days = max(1, min(int(days), 90))
    start, end = _period(days, today)

    account_transactions = _fetch_account_transactions(days, start, end)
    card_transactions = _fetch_credit_card_transactions(start, end)

    account_expenses = [tx for tx in account_transactions if _amount_cents(tx) < 0]
    account_income = [tx for tx in account_transactions if _amount_cents(tx) > 0]
    card_expenses = [tx for tx in card_transactions if _amount_cents(tx) != 0]
    all_expenses = account_expenses + card_expenses

    account_expense_total = sum(abs(_amount_cents(tx)) for tx in account_expenses)
    card_expense_total = sum(abs(_amount_cents(tx)) for tx in card_expenses)
    income_total = sum(_amount_cents(tx) for tx in account_income)
    expense_total = account_expense_total + card_expense_total

    lines = [
        f"<b>Resumo financeiro ({start.strftime('%d/%m')} a {end.strftime('%d/%m')})</b>",
        "",
        f"- Gastos em conta: {_format_brl(account_expense_total)}",
        f"- Compras no cartão: {_format_brl(card_expense_total)}",
        f"- Entradas em conta: {_format_brl(income_total)}",
        f"- Saída total observada: {_format_brl(expense_total)}",
    ]

    if all_expenses:
        lines.extend(["", "<b>Maiores categorias</b>"])
        for category, total in _summarize_categories(all_expenses)[:5]:
            lines.append(f"- {category}: {_format_brl(total)}")

        lines.extend(["", "<b>Maiores lançamentos</b>"])
        for tx in _top_items(all_expenses):
            source = _source_name(tx, "Origem não informada")
            lines.append(
                f"- {tx['date']} · {tx['description']} · {_format_brl(_amount_cents(tx))} · {source}"
            )
    else:
        lines.extend(["", "Não encontrei gastos nesse período."])

    budgets = _budget_lines()
    if budgets:
        lines.extend(["", "<b>Orçamentos do mês</b>", *budgets])

    return "\n".join(lines)
