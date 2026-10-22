# Pi-Ai-Agent: Personal Expense Tracker (Chile)

Track every purchase automatically using the notification emails Chilean banks
send after each card transaction. Each purchase gets a category (by rules, by
inference, or by asking you on Telegram) and is recorded in an Excel workbook
organized by **pay period** (28th of month N → 27th of month N+1).

---

## 1. Is reading emails the right approach?

Mostly yes. Here are the options compared:

| Source | Real-time | Effort | Reliability | Notes |
|---|---|---|---|---|
| **Bank notification emails** | ~seconds–minutes | Medium (one parser per bank) | High, as long as the email template doesn't change | Every major Chilean bank sends them (Banco de Chile, Santander, BCI, BancoEstado, Itaú, Scotiabank, Falabella, Tenpo, Mach…). **Recommended as the primary source.** |
| Phone push notifications (Android notification listener app → webhook) | Instant | High (Android app) | Medium | Good later addition. Not possible on iOS. |
| Monthly statement / *cartola* (PDF/XLSX download) | No | Low | Very high | **Use it for reconciliation**: it catches anything the emails missed. |
| Aggregators (e.g. Fintoc) / Open Finance (Ley Fintec 21.521) | Near-real-time | Low once available | High | Chile's open-finance system is being rolled out in phases. Worth checking again later. The design below keeps the ingestion layer pluggable so you can add it then. |
| Scraping bank websites | No | High | Low (captchas, 2FA, against ToS) | Avoid. |

**Decision:** use emails as the real-time source, the monthly *cartola* import
as a safety net, and a manual `/gasto` Telegram command for cash.

Known gaps you need to handle with emails:
- Some banks only email above a minimum amount, or only for credit (not debit). Turn on
  "notify me on every purchase" in each bank's app/website.
- Bank transfers (*transferencias*) arrive in a different email format. Parse those too.
- Reversals and refunds, purchases in installments (*cuotas*), and USD purchases.

---

## 2. Architecture

```
            ┌──────────────┐
 Bank ───►  │ Gmail / IMAP │
 emails     └──────┬───────┘
                   │ new message (poll every 60s or IMAP IDLE)
                   ▼
          ┌──────────────────┐   unknown sender/format
          │ Ingestor         │ ─────────────────────────► LLM extractor (fallback)
          │  └ bank parsers  │                                   │
          └────────┬─────────┘ ◄─────────────────────────────────┘
                   │ Transaction(amount, merchant, date, card, bank, msg_id)
                   ▼
          ┌──────────────────┐
          │ Deduplicator     │  (email Message-ID + hash of amount/merchant/time)
          └────────┬─────────┘
                   ▼
          ┌──────────────────┐   1. merchant memory (exact/alias match)
          │ Classifier       │   2. keyword rules (YAML)
          │                  │   3. LLM inference with a confidence score
          └────────┬─────────┘
       confident?  │  no ──────────► Telegram bot: "What was 'MERPAGO*XYZ' $12.990?"
                   │                  [Comida] [Transporte] [Supermercado] … [Otro ✏️]
                   │                         │ answer → saved to merchant memory
                   ▼                         ▼
          ┌──────────────────────────────────────┐
          │ SQLite (source of truth)             │
          └────────┬─────────────────────────────┘
                   ▼
          ┌──────────────────┐
          │ Excel exporter   │ → gastos.xlsx (one sheet per pay period + summary)
          └──────────────────┘
```

Everything runs as **one async Python process** (asyncio): the email poller,
the Telegram bot, and the scheduler. It can run on a Raspberry Pi as a
`systemd` service, or on any small VPS.

### Why SQLite plus Excel instead of writing straight into Excel?
- An `.xlsx` file can't be safely written while you have it open, and it's easy to corrupt.
- With SQLite as the source of truth, the Excel file can be regenerated at any
  time. Deduplication, edits from Telegram, and the reconciliation import all
  become simple queries.
- The exporter rewrites the workbook after each change, or on `/excel`, and can
  send the file to you on Telegram.
- Optional: sync to Google Sheets so you can see the data from your phone.

---

## 3. Components in detail

### 3.1 Email ingestion
- **Gmail:** use the Gmail API (OAuth, read-only scope `gmail.readonly`). Poll with
  a query such as `from:(notificaciones@bancochile.cl OR ...) newer_than:2d`. Use
  `historyId` to fetch only new messages. Later you can switch to push via
  Gmail `watch` + Pub/Sub if polling feels slow.
- **Other providers (Outlook, etc.):** use IMAP with an app password and `IDLE`
  (library: `imap-tools`).
- Recommended setup: create a Gmail filter that labels bank emails `gastos`, and
  query only that label.
- Store each processed `Message-ID` so you never process the same email twice.

### 3.2 Bank parsers (plugin per bank)
```python
class BankParser(Protocol):
    bank: str
    def matches(self, msg: EmailMessage) -> bool: ...   # by sender + subject
    def parse(self, msg: EmailMessage) -> Transaction | None: ...
```
- Parse the HTML body with BeautifulSoup and use regular expressions for the fields.
- CLP formats: `$12.345` (`.` separates thousands, there are no decimals), USD `US$ 12,34`,
  dates such as `22/10/2026 14:35`.
- Fields: `amount`, `currency`, `merchant_raw`, `datetime`, `card_last4`,
  `kind` (purchase / transfer / reversal), `installments`.
- **Fallback:** if no parser matches but the email looks financial, send the
  plain text to the LLM and ask for the same fields as JSON. Flag the result as
  "low trust" and confirm it via Telegram.
- Save real (anonymized) emails as **test fixtures**. Each parser gets
  unit tests against them, so you'll notice when a bank changes its template.

### 3.3 Classification (in order)
1. **Merchant memory**: a table `merchant_raw → normalized_name, category`.
   It fills up from your Telegram answers. After a few weeks most
   purchases will be classified here at no cost.
2. **Rules**: `categories.yaml` with keywords/regex
   (`COPEC|SHELL|PETROBRAS → Transporte/Bencina`, `LIDER|JUMBO|UNIMARC → Supermercado`,
   `UBER\s*EATS|RAPPI|PEDIDOSYA → Comida/Delivery`).
3. **LLM inference**: send the merchant string, amount, time, and your category list.
   The model returns `{category, confidence, normalized_merchant}`. Use a
   small, cheap model such as Claude Haiku 5.5 via the `anthropic` SDK. Only the merchant
   name and amount are sent, no personal data.
4. **Ask on Telegram** if `confidence < threshold` (for example 0.75), or for payment
   aggregators that hide the real merchant (`MERPAGO*`, `FLOW*`, `KHIPU`,
   `TRANSBANK`, `PAYU`).

Suggested categories (editable): Supermercado, Comida/Restaurantes, Delivery,
Transporte, Bencina, Cuentas/Servicios, Arriendo/Dividendo, Salud, Educación,
Entretenimiento, Suscripciones, Ropa, Hogar, Viajes, Regalos, Transferencias,
Otros.

### 3.4 Telegram bot
- Library: `python-telegram-bot` v21+ (async).
- Use **long polling** rather than a webhook. It needs no public URL or HTTPS
  certificate, which suits a Pi at home. A webhook is only worth it on a VPS.
- **Security:** the bot replies only to your `chat_id` (whitelist) and ignores
  everyone else.
- **Question flow:** for a purchase it can't classify, it sends a message with inline
  category buttons, plus "✏️ Otro" (free text, so you can reply "almuerzo con
  el equipo") and "🚫 No es gasto" (for example a transfer between your own accounts).
  Unanswered questions are stored as `pending` and reminded once a day.
- **Commands:**
  - `/resumen`: total spent in the current period, split by category, compared with the last period
  - `/gasto 5000 feria verduras`: manual entry (cash)
  - `/editar <id>` and `/deshacer`: fix the last record
  - `/pendientes`: purchases still waiting for a category
  - `/excel`: generate and send the `.xlsx` file
  - `/presupuesto Comida 150000`: optional per-category budget with alerts at 80% and 100%
- **Notifications:** an optional short confirmation for every auto-classified
  purchase ("✅ $4.500 Starbucks → Comida"), a weekly summary, and an end-of-period
  report on the 27th.

### 3.5 Pay period logic
```python
def period_for(d: date, payday: int = 28) -> tuple[date, date]:
    """Period runs from `payday` of month N to the day before `payday` of month N+1."""
    if d.day >= payday:
        start = d.replace(day=payday)
    else:
        start = (d.replace(day=1) - timedelta(days=1)).replace(day=payday)
    end = (start + relativedelta(months=1)) - timedelta(days=1)
    return start, end
```
- Make `payday` a config setting (it's 28 now but could change). Store each period's
  start date on every transaction, so changing the payday doesn't rewrite old periods.
- Edge case: `payday` > 28 (Feb). Clamp to the last day of the month.
- Optional: if the 28th falls on a weekend or holiday and you're actually paid the
  previous business day, use `holidays` (country `CL`) to move the start of the period.
- Use the **purchase date** from the email, not the date the email arrived.

### 3.6 Excel output (`openpyxl`)
- One sheet per period, named for example `2026-10-28 → 2026-11-27`, with columns:
  `Fecha | Hora | Comercio | Monto (CLP) | Categoría | Banco | Tarjeta | Cuotas | Fuente | Nota | ID`
- A `Resumen` sheet with a row per period, a column per category, and totals.
  Optional: a bar chart.
- Format CLP as `"$"#,##0` and use the Excel table style, so filters work.
- USD purchases: store the original amount plus a CLP amount converted with the
  daily dólar observado (mindicador.cl API). Mark them as "estimado" until the
  statement confirms the amount.

### 3.7 Reconciliation (monthly)
- `/importar` (send the *cartola* XLSX/PDF to the bot) or a CLI command parses the
  statement and matches lines with existing transactions by amount ± date.
- Anything missing gets inserted (source `cartola`), and the bot asks you to classify it.
- This also tells you which purchases your banks don't email you about.

---

## 4. Data model (SQLite)

```sql
transactions(
  id INTEGER PK, period_start DATE, occurred_at DATETIME,
  amount_clp INTEGER, amount_orig REAL, currency TEXT,
  merchant_raw TEXT, merchant TEXT, category TEXT,
  bank TEXT, card_last4 TEXT, kind TEXT, installments INTEGER,
  source TEXT,            -- email | llm_email | manual | cartola
  classified_by TEXT,     -- memory | rule | llm | user
  confidence REAL, status TEXT,  -- ok | pending | ignored
  email_msg_id TEXT UNIQUE, dedup_hash TEXT UNIQUE, note TEXT,
  created_at DATETIME
)
merchants(merchant_raw TEXT PK, merchant TEXT, category TEXT, times_seen INTEGER)
pending_questions(tx_id INTEGER, telegram_msg_id INTEGER, asked_at DATETIME)
processed_emails(msg_id TEXT PK, processed_at DATETIME, result TEXT)
```

---

## 5. Project structure

```
Pi-Ai-Agent/
├── pyproject.toml            # uv / pip, Python 3.12
├── .env.example              # TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY, ...
├── config.yaml               # payday, banks enabled, confidence threshold, paths
├── categories.yaml           # categories + keyword rules
├── src/expense_tracker/
│   ├── main.py               # starts the asyncio loop: poller + bot + scheduler
│   ├── config.py             # pydantic-settings
│   ├── models.py             # Transaction dataclass
│   ├── db.py                 # SQLite (sqlite3 or SQLModel)
│   ├── periods.py            # pay period logic
│   ├── ingest/
│   │   ├── gmail.py          # Gmail API client
│   │   ├── imap.py           # IMAP alternative
│   │   └── parsers/
│   │       ├── base.py
│   │       ├── bancochile.py
│   │       ├── santander.py
│   │       ├── bci.py
│   │       └── llm_fallback.py
│   ├── classify/
│   │   ├── memory.py
│   │   ├── rules.py
│   │   └── llm.py
│   ├── bot/
│   │   ├── handlers.py       # commands + callback buttons
│   │   └── notifications.py
│   ├── export/excel.py
│   └── reconcile/cartola.py
├── tests/
│   ├── fixtures/emails/      # anonymized .eml samples per bank
│   ├── test_parsers.py
│   ├── test_periods.py
│   └── test_classifier.py
└── deploy/expense-tracker.service   # systemd unit
```

**Main libraries:** `google-api-python-client` + `google-auth-oauthlib`, `imap-tools`,
`beautifulsoup4`, `python-telegram-bot`, `anthropic`, `openpyxl`,
`pydantic-settings`, `python-dateutil`, `holidays`, `APScheduler` (or the job queue
built into PTB), `pytest`.

---

## 6. Milestones

| # | Milestone | Done when |
|---|---|---|
| 0 | **Collect samples.** Forward 3–5 notification emails from each of your banks. Turn on "notify every purchase" in each bank. | Fixtures exist for every bank you use |
| 1 | **Parsing core.** `models`, `periods`, one bank parser, tests | `pytest` passes against real fixtures |
| 2 | **Gmail ingestion + SQLite + dedup** (CLI: `python -m expense_tracker ingest --since 30d`) | Last month's purchases are in the DB with no duplicates |
| 3 | **Excel export** | `gastos.xlsx` with period sheets and a summary |
| 4 | **Telegram bot**: notifications, classification questions, `/resumen`, `/gasto` | You can classify a purchase from your phone |
| 5 | **Classifier**: memory, then rules, then LLM | Fewer than 20% of purchases need a question after 2 weeks |
| 6 | **Deploy** on the Pi as a systemd service, with logs and a health check ("I haven't seen an email in 3 days" alert) | Runs unattended for one full period |
| 7 | **Extras**: *cartola* reconciliation, budgets, USD conversion, more banks, Google Sheets sync | As needed |

A minimum usable version is milestones 1–4. It's roughly a few weekends of work.

---

## 7. Security and privacy
- Use read-only Gmail scopes. Keep the OAuth token and `.env` out of git, with `chmod 600`.
- Only your `chat_id` can talk to the bot.
- The LLM receives only the merchant string and amount, never the full email
  (except in the fallback extractor, which strips names, RUT, and account numbers first).
  For maximum privacy you could run a local model, at the cost of accuracy and
  speed on a Pi.
- Back up `expenses.db` (for example a nightly copy to Google Drive or another machine).

---

## 8. Open questions to decide
1. Which banks and cards do you use, and which email provider receives the bank emails?
2. Where will it run? (Raspberry Pi at home, a VPS, or your laptop)
3. Do you need the Excel file locally, or would Google Sheets be more convenient?
4. If the 28th falls on a weekend, are you paid earlier? This affects the period boundaries.
5. Should transfers to other people count as expenses (rent, shared bills)?
