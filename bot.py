import os
import json
import logging
import base64
import html
import re
import unicodedata
from io import BytesIO
from datetime import date, datetime
from typing import Optional
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
import organizze_client as org
import storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

REQUIRED_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "ORGANIZZE_EMAIL",
    "ORGANIZZE_API_TOKEN",
    "OPENAI_API_KEY",
)

CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")
openai_client = None


def validate_required_env() -> None:
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            f"{', '.join(missing)}. Configure them in Railway Variables."
        )


def get_openai_client() -> OpenAI:
    global openai_client
    if openai_client is None:
        validate_required_env()
        openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return openai_client

SYSTEM_PROMPT = """You are a personal finance assistant connected to the user's Organizze account.
You help them manage finances by calling tools when needed.

Available tools:
- get_accounts: List bank accounts. Organizze does not expose current bank-account balances through this endpoint.
- get_transactions: List recent transactions (optional: days=30, account_id)
- create_transaction: Add a transaction (required: description, amount_cents, date YYYY-MM-DD, account_id; optional: category_id, notes)
- get_categories: List all categories
- get_budgets: List current month budgets and usage

Rules:
- Never tell the user a transaction was created unless the tool result confirms it.
- The app enforces confirmation before create_transaction. If the user asks to create a transaction, call create_transaction with the draft params; the app will save it as a pending action and ask for confirmation before writing to Organizze.
- Dates default to today if not specified
- Always respond in the same language the user writes in
- Be concise and friendly
- When listing transactions, format nicely with clear income/expense labels
- For emphasis, use Telegram HTML tags like <b>bold</b> and <i>italic</i>. Do not use Markdown emphasis like **bold**.
- Negative amounts = expense, positive = income in Organizze
- When a receipt is extracted, present a clear summary before confirming

Respond ONLY with valid JSON in this format:
{
  "intent": "tool_call | final_answer | ask_user | confirm_action",
  "tool": null,
  "params": {},
  "message": "Your reply to the user",
  "confidence": "high | medium | low"
}
If no tool is needed, set tool to null and just reply in message.
If you need to call a tool first before replying, set the tool and leave message as empty string.
"""

RECEIPT_EXTRACTION_PROMPT = """You are a receipt data extractor. Analyze this receipt image and return ONLY a JSON object with no extra text.

Extract:
{
  "store": "store or merchant name",
  "total_brl": 0.00,
  "date": "YYYY-MM-DD or null if not visible",
  "items": ["item1", "item2"],
  "category_hint": "one of: alimentação, transporte, saúde, educação, lazer, moradia, vestuário, outros"
}

Rules:
- total_brl should be the final total (after discounts, with taxes)
- date: use the receipt date if visible, else null
- items: list up to 5 main items; if too many just summarize (e.g. "groceries x12")
- If you can't read the receipt clearly, still return your best guess with available info
"""

# Short explicit replies accepted while a pending action is waiting.
AFFIRMATIVE_CONFIRMATIONS = {"sim", "s", "yes", "y", "confirmo", "confirma", "pode criar", "pode lancar", "ok"}
NEGATIVE_CONFIRMATIONS = {"nao", "n", "no", "cancelar", "cancela", "deixa", "nao lanca"}


def _normalize_reply(text: str) -> str:
    without_accents = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(char)
    )
    return " ".join(without_accents.strip().split())


def _telegram_html(text: str) -> str:
    """Escape text for Telegram HTML and convert Markdown bold only."""
    allowed_tags = {
        "<b>": "__TG_B_OPEN__",
        "</b>": "__TG_B_CLOSE__",
        "<i>": "__TG_I_OPEN__",
        "</i>": "__TG_I_CLOSE__",
    }
    formatted = text
    for tag, token in allowed_tags.items():
        formatted = formatted.replace(tag, token)

    formatted = html.escape(formatted, quote=False)
    formatted = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", formatted, flags=re.DOTALL)

    for tag, token in allowed_tags.items():
        formatted = formatted.replace(token, tag)

    return formatted


async def send_message(update: Update, text: str) -> None:
    await update.message.reply_text(_telegram_html(text), parse_mode=ParseMode.HTML)


def _format_brl(amount_cents: int) -> str:
    amount = abs(amount_cents) / 100
    prefix = "-" if amount_cents < 0 else "+"
    return f"{prefix}R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _format_pending_transaction(params: dict) -> str:
    account_name = params.get("account_name")
    if not account_name and params.get("account_id"):
        account_name = _get_account_name(int(params["account_id"]))

    lines = [
        "Revise antes de eu lançar:",
        f"- Descrição: {params.get('description', 'sem descrição')}",
        f"- Valor: {_format_brl(int(params.get('amount_cents', 0)))}",
        f"- Data: {params.get('date', 'não informada')}",
        f"- Conta: {account_name or params.get('account_id', 'não informada')}",
    ]
    if params.get("category_id"):
        lines.append(f"- Categoria ID: {params['category_id']}")
    if params.get("notes"):
        lines.append(f"- Observações: {params['notes']}")
    lines.append("")
    lines.append("Responda `sim` para confirmar ou `cancelar` para descartar.")
    return "\n".join(lines)


def _resolve_account_id(value) -> Optional[int]:
    if isinstance(value, int):
        return value

    if isinstance(value, str):
        if value.isdigit():
            return int(value)

        accounts = org.get_accounts()
        normalized = value.strip().lower()
        for account in accounts:
            if account["name"].strip().lower() == normalized:
                return int(account["id"])

    return None


def _get_account_name(account_id: int) -> str:
    for account in org.get_accounts():
        if int(account["id"]) == account_id:
            return account["name"]
    return ""


def _transaction_write_params(params: dict) -> dict:
    allowed = {"description", "amount_cents", "date", "account_id", "category_id", "notes", "paid"}
    return {key: value for key, value in params.items() if key in allowed}


def _normalize_transaction_params(params: dict) -> tuple[Optional[dict], list[str]]:
    normalized = dict(params)
    errors = []

    description = str(normalized.get("description") or "").strip()
    if not description:
        errors.append("descrição")
    normalized["description"] = description

    try:
        normalized["amount_cents"] = int(normalized["amount_cents"])
    except (KeyError, TypeError, ValueError):
        errors.append("valor")

    account_id = _resolve_account_id(normalized.get("account_id"))
    if account_id is None:
        errors.append("conta")
    else:
        normalized["account_id"] = account_id

    date_text = str(normalized.get("date") or "").strip()
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
        normalized["date"] = date_text
    except ValueError:
        errors.append("data")

    if normalized.get("category_id") in ("", None):
        normalized.pop("category_id", None)
    elif not isinstance(normalized.get("category_id"), int):
        try:
            normalized["category_id"] = int(normalized["category_id"])
        except (TypeError, ValueError):
            errors.append("categoria")

    if errors:
        return None, errors

    return normalized, []


def handle_pending_confirmation(user_id: int, user_message: str) -> Optional[str]:
    pending = storage.get_pending_action(user_id)
    if not pending:
        return None

    normalized = _normalize_reply(user_message)
    if normalized in NEGATIVE_CONFIRMATIONS:
        storage.resolve_pending_action(pending["id"], "cancelled")
        message = "Tudo bem, descartei esse lançamento."
        storage.append_message(user_id, "assistant", message)
        return message

    if normalized in AFFIRMATIVE_CONFIRMATIONS:
        if pending["action_type"] != "create_transaction":
            storage.resolve_pending_action(pending["id"], "cancelled")
            message = "Não reconheci essa ação pendente, então descartei por segurança."
            storage.append_message(user_id, "assistant", message)
            return message

        try:
            result = org.create_transaction(**_transaction_write_params(pending["params"]))
            storage.resolve_pending_action(pending["id"], "confirmed")
            message = (
                "Lançamento criado:\n"
                f"- {result['description']}\n"
                f"- {_format_brl(int(result['amount_brl'] * 100))}\n"
                f"- Data: {result['date']}\n"
                f"- Conta: {result.get('account') or 'não informada'}"
            )
        except Exception as e:
            logger.error(f"Pending action error: {e}")
            message = f"Não consegui criar o lançamento: {e}"

        storage.append_message(user_id, "assistant", message)
        return message

    message = (
        "Tenho um lançamento aguardando confirmação. "
        "Responda `sim` para salvar ou `cancelar` para descartar antes de começarmos outro."
    )
    storage.append_message(user_id, "assistant", message)
    return message

def call_tool(tool: str, params: dict) -> str:
    """Execute an Organizze tool and return a string result."""
    try:
        if tool == "get_accounts":
            return json.dumps(org.get_accounts())
        elif tool == "get_transactions":
            return json.dumps(org.get_transactions(**params))
        elif tool == "create_transaction":
            return json.dumps({"error": "create_transaction must be confirmed through a pending action"})
        elif tool == "get_categories":
            return json.dumps(org.get_categories())
        elif tool == "get_budgets":
            return json.dumps(org.get_budgets())
        else:
            return json.dumps({"error": f"Unknown tool: {tool}"})
    except Exception as e:
        return json.dumps({"error": str(e)})

def extract_receipt(image_bytes: bytes) -> dict:
    """Use GPT-4o vision to extract structured data from a receipt image."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    response = get_openai_client().chat.completions.create(
        model=VISION_MODEL,
        max_tokens=500,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}",
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": RECEIPT_EXTRACTION_PROMPT},
                ],
            }
        ],
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    return json.loads(raw)

def ask_llm(user_id: int, user_message: str) -> str:
    """Send message to LLM, handle tool calls, return final text response."""
    storage.append_message(user_id, "user", user_message)

    pending_response = handle_pending_confirmation(user_id, user_message)
    if pending_response:
        return pending_response

    trimmed = storage.get_recent_messages(user_id, limit=20)

    for _ in range(5):
        response = get_openai_client().chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": f"{SYSTEM_PROMPT}\nToday is {date.today().isoformat()}.",
                }
            ] + trimmed,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            storage.append_message(user_id, "assistant", raw)
            return raw

        tool = parsed.get("tool")
        message = parsed.get("message", "")
        params = parsed.get("params", {})

        if tool:
            if tool == "create_transaction":
                normalized_params, errors = _normalize_transaction_params(params)
                if normalized_params is None:
                    reply = (
                        "Preciso completar alguns dados antes de preparar esse lançamento: "
                        f"{', '.join(errors)}. Pode me mandar esses detalhes?"
                    )
                    storage.append_message(user_id, "assistant", reply)
                    return reply

                pending_id = storage.create_pending_action(user_id, tool, normalized_params)
                reply = _format_pending_transaction(normalized_params)
                logger.info(f"Pending action {pending_id} created for user {user_id}")
                storage.append_message(user_id, "assistant", reply)
                return reply

            tool_result = call_tool(tool, params)
            logger.info(f"Tool {tool} called, result: {tool_result[:200]}")
            trimmed.append({"role": "assistant", "content": raw})
            trimmed.append({"role": "user", "content": f"[Tool result for {tool}]: {tool_result}"})
            storage.append_message(user_id, "assistant", raw)
            storage.append_message(user_id, "user", f"[Tool result for {tool}]: {tool_result}")
        else:
            storage.append_message(user_id, "assistant", message)
            return message

    return "Não consegui completar a operação. Tente novamente."

def is_authorized(user_id: int) -> bool:
    allowed_ids = os.environ.get("ALLOWED_USER_IDS", "")
    if not allowed_ids:
        return True
    allowed = [int(x.strip()) for x in allowed_ids.split(",") if x.strip()]
    return user_id in allowed

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await send_message(update, "⛔ Acesso não autorizado.")
        return

    await update.message.chat.send_action("typing")

    try:
        reply = ask_llm(user_id, update.message.text)
    except Exception as e:
        logger.error(f"Error: {e}")
        reply = f"❌ Erro interno: {e}"

    await send_message(update, reply)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await send_message(update, "Acesso não autorizado.")
        return

    await send_message(update, 
        "Oi. Eu ajudo a consultar o Organizze e preparar lançamentos por aqui.\n\n"
        "Você pode perguntar saldo, gastos recentes, categorias e orçamentos. "
        "Quando eu preparar um lançamento, sempre vou pedir confirmação antes de salvar."
    )


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await send_message(update, "Acesso não autorizado.")
        return

    await send_message(update, 
        "Exemplos:\n"
        "- qual meu saldo?\n"
        "- gastos dos últimos 7 dias\n"
        "- gastei R$ 45 no mercado hoje, conta Nubank\n"
        "- quais categorias tenho?\n"
        "- como estão meus orçamentos?\n\n"
        "Para lançamentos, eu monto um rascunho e só salvo depois que você responder `sim`."
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle receipt photos — extract data via vision then confirm with user."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await send_message(update, "⛔ Acesso não autorizado.")
        return

    await update.message.chat.send_action("typing")

    try:
        # Download highest-resolution version of the photo
        photo = update.message.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        buf = BytesIO()
        await tg_file.download_to_memory(buf)
        image_bytes = buf.getvalue()

        # Extract receipt data via GPT-4o vision
        receipt = extract_receipt(image_bytes)
        logger.info(f"Receipt extracted: {receipt}")

        # Build a synthetic message to feed into the main conversation loop
        caption = update.message.caption or ""
        date_str = receipt.get("date") or "hoje"
        items_str = ", ".join(receipt.get("items", [])) or "itens não identificados"

        synthetic_message = (
            f"[Comprovante/Nota fiscal recebida]\n"
            f"Estabelecimento: {receipt.get('store', 'desconhecido')}\n"
            f"Total: R$ {receipt.get('total_brl', 0):.2f}\n"
            f"Data: {date_str}\n"
            f"Itens: {items_str}\n"
            f"Categoria sugerida: {receipt.get('category_hint', 'outros')}\n"
            f"Observação do usuário: {caption or 'nenhuma'}\n\n"
            f"Confirme os dados com o usuário e pergunte em qual conta lançar antes de criar a transação."
        )

        reply = ask_llm(user_id, synthetic_message)

    except json.JSONDecodeError:
        reply = (
            "🧾 Recebi a imagem mas não consegui ler o comprovante.\n"
            "Tente tirar uma foto com melhor iluminação, ou digite o valor manualmente."
        )
    except Exception as e:
        logger.error(f"Photo error: {e}")
        reply = f"❌ Erro ao processar a imagem: {e}"

    await send_message(update, reply)

def main():
    validate_required_env()
    storage.init_db()
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    logger.info("Bot started with receipt support!")
    app.run_polling()

if __name__ == "__main__":
    main()
