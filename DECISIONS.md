# Product and Architecture Decisions

## 1. Chat is the interface; the app owns financial workflow state

This bot should behave like a small financial workflow engine with a chat interface, not like a free-form chatbot that happens to call tools.

The LLM may interpret messages, draft transactions, inspect Organizze data, and ask follow-up questions. The Python app owns durable state, pending actions, confirmation, writes, and auditability.

## 2. Confirm before any write

Financial writes must be confirm-before-write by default.

For example:

1. User sends: `gastei 45 reais no mercado hoje`
2. Assistant extracts a draft transaction.
3. App stores a pending `create_transaction` action.
4. Assistant asks the user to confirm the amount, date, account, and category.
5. Only an explicit confirmation creates the Organizze transaction.

Read-only tools can run immediately. Write tools should become pending actions first.

## 3. Persist conversation and pending actions

In-memory history is too fragile for a finance assistant because Railway restarts, deploys, and crashes would erase context.

Conversation history and pending actions should be persisted with SQLite using the Python stdlib `sqlite3` module. This keeps deployment simple while giving us enough durability for:

- continuing a flow after restart;
- auditing why an action was created;
- keeping pending transaction drafts separate from the LLM prompt.

## 4. Use explicit pending actions

Pending operations should be stored as structured records rather than inferred from recent chat text.

Example:

```json
{
  "type": "create_transaction",
  "params": {
    "description": "Mercado Extra",
    "amount_cents": -4590,
    "date": "2026-05-09",
    "account_id": 123
  },
  "status": "awaiting_confirmation"
}
```

This makes multi-step flows deterministic, especially receipt extraction where the bot may need account/category details before saving.

## 5. Use a richer deterministic LLM response schema

The LLM must still return JSON only, but the schema should separate assistant behavior from tool dispatch:

```json
{
  "intent": "tool_call | final_answer | ask_user | confirm_action",
  "tool": "tool_name_or_null",
  "params": {},
  "message": "text for the user",
  "confidence": "high | medium | low"
}
```

This lets the app decide when to execute tools, ask the user, create pending actions, or stop the loop.

## 6. Cache low-change reference data

Organizze accounts and categories are reference data. They should be cached with a short TTL to reduce latency, repeated API calls, and repeated LLM tool loops.

The API remains the source of truth. The cache is only a convenience.

## 7. Scope all durable data by Telegram user

Even if the first version is single-user, all stored rows should be keyed by `telegram_user_id`.

This avoids rewriting persistence if the bot later supports multiple allowed users or multiple Organizze credential profiles.

## 8. Keep model choices configurable

The default can remain:

- chat/tool loop: `gpt-4o-mini`
- receipt extraction: `gpt-4o`

But both should be environment-configurable so we can test cheaper or stronger models without code changes.

## 9. Add escape hatches early

Finance bots need recovery paths. Delete/edit transaction support and `undo last transaction created by the bot` should stay near the top of the roadmap.

Until those are implemented, the bot should be conservative about writes and clear when a transaction has actually been created.
