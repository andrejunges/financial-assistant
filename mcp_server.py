"""
Read-only MCP server for the personal Organizze financial assistant.

This exposes the same Organizze read surface used by the Telegram bot through
Streamable HTTP, so Claude can inspect accounts, transactions, cards, invoices,
categories, tags, and budgets without introducing a second financial backend.
"""

import os
from contextlib import asynccontextmanager
from typing import Optional

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
import uvicorn

import organizze_client as org


MCP_HOST = os.environ.get("MCP_HOST")
if MCP_HOST is None:
    MCP_HOST = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
MCP_PORT = int(os.environ.get("PORT", os.environ.get("MCP_PORT", "8000")))

mcp = FastMCP(
    "Personal Financial Assistant",
    instructions=(
        "Read-only access to the user's Organizze personal finance data. "
        "Organizze is the source of truth. Do not claim writes are available "
        "through this connector yet."
    ),
    stateless_http=True,
    json_response=True,
    host=MCP_HOST,
    port=MCP_PORT,
)


def _bounded_days(days: int, *, minimum: int = 1, maximum: int = 730) -> int:
    return max(minimum, min(int(days), maximum))


def _validate_required_env() -> None:
    missing = [
        name
        for name in ("ORGANIZZE_EMAIL", "ORGANIZZE_API_TOKEN")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )


@mcp.tool()
def health_check() -> dict:
    """Check whether the MCP server has the required Organizze configuration."""
    missing = [
        name
        for name in ("ORGANIZZE_EMAIL", "ORGANIZZE_API_TOKEN")
        if not os.environ.get(name)
    ]
    return {
        "ok": not missing,
        "missing_env": missing,
        "mode": "read_only",
    }


@mcp.tool()
def get_accounts() -> list[dict]:
    """List Organizze bank accounts. Current balances are not exposed by this API."""
    _validate_required_env()
    return org.get_accounts()


@mcp.tool()
def get_transactions(days: int = 30, account_id: Optional[int] = None) -> list[dict]:
    """List recent Organizze transactions, optionally scoped to one bank account."""
    _validate_required_env()
    return org.get_transactions(days=_bounded_days(days), account_id=account_id)


@mcp.tool()
def get_credit_cards() -> list[dict]:
    """List Organizze credit cards."""
    _validate_required_env()
    return org.get_credit_cards()


@mcp.tool()
def get_credit_card_invoices(
    credit_card_id: Optional[int] = None,
    credit_card_name: Optional[str] = None,
) -> list[dict]:
    """List invoices for a credit card by id or approximate name."""
    _validate_required_env()
    return org.get_credit_card_invoices(
        credit_card_id=credit_card_id,
        credit_card_name=credit_card_name,
    )


@mcp.tool()
def get_credit_card_invoice(
    credit_card_id: Optional[int] = None,
    invoice_id: Optional[int] = None,
    credit_card_name: Optional[str] = None,
) -> dict:
    """Return one credit-card invoice, including its transactions."""
    _validate_required_env()
    return org.get_credit_card_invoice(
        credit_card_id=credit_card_id,
        invoice_id=invoice_id,
        credit_card_name=credit_card_name,
    )


@mcp.tool()
def get_credit_card_monthly_expense(
    credit_card_id: Optional[int] = None,
    credit_card_name: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    include_transactions: bool = True,
) -> dict:
    """Return the credit-card invoice spend for a month or the current invoice."""
    _validate_required_env()
    return org.get_credit_card_monthly_expense(
        credit_card_id=credit_card_id,
        credit_card_name=credit_card_name,
        year=year,
        month=month,
        include_transactions=include_transactions,
    )


@mcp.tool()
def get_categories() -> list[dict]:
    """List Organizze categories."""
    _validate_required_env()
    return org.get_categories()


@mcp.tool()
def get_tags(days: int = 365, include_credit_cards: bool = True) -> list[dict]:
    """List tags observed in recent transactions and credit-card invoices."""
    _validate_required_env()
    return org.get_tags(
        days=_bounded_days(days, maximum=1095),
        include_credit_cards=include_credit_cards,
    )


@mcp.tool()
def get_budgets() -> list[dict]:
    """List current month Organizze budgets and usage."""
    _validate_required_env()
    return org.get_budgets()


async def health(request: Request) -> JSONResponse:
    return JSONResponse(health_check())


class BearerTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        token = os.environ.get("MCP_AUTH_TOKEN")
        if not token:
            return await call_next(request)

        expected = f"Bearer {token}"
        if request.headers.get("authorization") != expected:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: Starlette):
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Mount("/", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)
app.add_middleware(BearerTokenMiddleware)


if __name__ == "__main__":
    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT)
