# Organizze Telegram Bot

A Telegram bot with a GPT-4o-mini brain that talks to your Organizze account.  
Say things like *"gastei 45 reais no mercado hoje"* and it drafts the transaction for confirmation before saving.

---

## What it can do

| Ask | What happens |
|-----|-------------|
| "quais contas tenho?" | Lists bank accounts |
| "gastos dos últimos 7 dias" | Lists recent transactions |
| "gastei R$45 no mercado hoje, conta Nubank" | Drafts an expense and asks for confirmation |
| "gastei R$45 no mercado #trabalho" | Drafts an expense with tags and asks for confirmation |
| "remover lançamento duplicado" | Finds/drafts a deletion and asks for confirmation |
| "quais categorias tenho?" | Lists all categories |
| "como estão meus orçamentos?" | Shows budget vs actual this month |
| receipt photo | Extracts receipt details and asks before saving |

Transactions are never created or deleted immediately from an LLM response. The app stores a pending action and only calls Organizze after an explicit confirmation, either through the Telegram buttons or a typed `sim`.

---

## Setup (10 minutes)

### 1. Create your Telegram bot
1. Open Telegram, message **@BotFather**
2. Send `/newbot`, follow the prompts
3. Copy the **bot token** you receive

### 2. Get your Organizze API token
1. Log in at [organizze.com.br](https://organizze.com.br)
2. Go to **Configurações → Conta → API**
3. Copy your API token

### 3. Get your OpenAI API key
- [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
- gpt-4o-mini costs ~$0.00015/1k tokens — essentially free for personal use

### 4. Get your Telegram user ID (for security)
- Message **@userinfobot** on Telegram
- Copy your numeric ID (e.g. `123456789`)

### 5. Configure environment
```bash
cp .env.example .env
# Edit .env and fill in all values
```

### 6. Install & run locally
```bash
pip install -r requirements.txt

# Load env vars (Linux/Mac)
export $(cat .env | xargs)

# Or on Windows PowerShell:
# Get-Content .env | ForEach-Object { $name, $value = $_ -split '=', 2; Set-Item "env:$name" $value }

python3 bot.py
```

---

## Deploy for free

### Option A: Railway (easiest)
1. Push this folder to a GitHub repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add your env vars in the **Variables** tab:
   - `TELEGRAM_BOT_TOKEN`
   - `ORGANIZZE_EMAIL`
   - `ORGANIZZE_API_TOKEN`
   - `OPENAI_API_KEY`
   - `ALLOWED_USER_IDS`
4. Railway auto-detects the `Procfile` and runs `python bot.py`
5. Set the service type to **Worker** (no web server needed)

### Option B: Render
1. Push to GitHub
2. [render.com](https://render.com) → New **Background Worker**
3. Build command: `pip install -r requirements.txt`
4. Start command: `python bot.py`
5. Add env vars in the dashboard

### Option C: Any Linux VPS (Oracle Free Tier, etc.)
```bash
git clone your-repo && cd organizze_bot
pip install -r requirements.txt
cp .env.example .env && nano .env  # fill in values

# Run as a background service
nohup python bot.py &

# Or with systemd for auto-restart on reboot (recommended)
```

---

## Customization

**Change the LLM models** with environment variables:
```bash
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_VISION_MODEL=gpt-4o
```

**Change the SQLite database path**:
```bash
HISTORY_DB_PATH=financial_assistant.sqlite3
```

**Change the default account used when a new transaction omits the account**:
```bash
DEFAULT_ACCOUNT_NAME=BTG
```

**Change the reference data cache TTL**:
```bash
REFERENCE_CACHE_TTL_SECONDS=300
```

**Add more tools** in `organizze_client.py` and list them in `SYSTEM_PROMPT`.

---

## Raycast quick entry

This repo also includes a local Raycast extension for fast expense capture with autocomplete backed by recent Organizze transactions.

Warm the local suggestion cache:
```bash
python3 finance_cli.py refresh-templates --days 180
```

Try suggestions from the CLI:
```bash
python3 finance_cli.py suggest "Bancarela 57"
```

Run the Raycast extension locally:
```bash
cd raycast
npm install
npm run dev
```

Raycast commands:

- `Quick Expense`: type a short entry like `Bancarela 57`, choose a suggested description, confirm, and create the expense.
- `Refresh Expense Suggestions`: refreshes local autocomplete templates from recent Organizze transactions.

The Raycast extension calls `finance_cli.py`, which reads `.env` directly. It uses the same `DEFAULT_ACCOUNT_NAME` default as the Telegram bot.

---

## Project structure

```
organizze_bot/
├── bot.py                 # Telegram bot + LLM loop
├── finance_cli.py         # Local CLI for Raycast quick entry
├── organizze_client.py    # Organizze API wrapper
├── raycast/               # Raycast extension
├── storage.py             # SQLite history + pending actions
├── DECISIONS.md           # Product and architecture decisions
├── requirements.txt
├── Procfile               # For Railway/Render
├── .env.example           # Environment variable template
└── README.md
```

---

## Security note
Always set `ALLOWED_USER_IDS` to your Telegram ID. Otherwise anyone who finds your bot can read and write your financial data.

## Current architecture

- Polling, not webhooks.
- JSON-only LLM responses for deterministic dispatch.
- Tool loop capped at 5 iterations.
- SQLite-backed conversation history and pending actions.
- App-owned confirm-before-write flow for financial mutations.
- Default account for omitted transaction accounts, configurable with `DEFAULT_ACCOUNT_NAME`.
- Short TTL cache for accounts and categories.
- Configurable OpenAI chat and vision models.
