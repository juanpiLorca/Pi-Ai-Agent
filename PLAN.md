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
 Bank ───►  │ Graph / IMAP │
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
The bank emails currently arrive at an **Outlook mailbox (`@uc.cl`, a Microsoft
365 work/school account)**. That affects how the Pi can read them:

- Microsoft has turned off password logins (basic auth) for IMAP on Microsoft 365, so
  an app password won't work. The supported way is the **Microsoft Graph API with
  OAuth**:
  - Register an app in the Microsoft Entra admin center (free). It needs the
    delegated permissions `Mail.Read` and `offline_access`.
  - Log in once on the Pi with the **device-code flow** (library `msal`). The Pi
    prints a code, you open `microsoft.com/devicelogin` on your phone, and the Pi
    stores a refresh token.
  - Poll `/me/mailFolders/{folder}/messages/delta` every 60 seconds. The delta
    query returns only new messages.
- **Risks with a university account:**
  - The UC tenant may block users from consenting to third-party apps, which would
    need IT approval.
  - It may block automatic forwarding to outside addresses.
  - The account may be closed when you leave the university.
- **Update (Oct 2026):** UC doesn't let users register apps, so the agent uses option 2
  (a dedicated Gmail account over IMAP). See docs/SETUP.md.
- **Decision:** write ingestion behind a `MailSource` interface with two
  implementations:
  1. `GraphMailSource`: use it now with `@uc.cl`. If login works, you're done.
  2. `ImapMailSource`: use it with a **dedicated mailbox you control**, for example
     a new Gmail account with 2FA and an app password, or an Outlook.com account
     with OAuth IMAP. When `@uc.cl` stops being an option, change the
     notification email in Banco de Chile and BICE (in each bank's profile/contact
     settings, or by asking their support) to that dedicated address. Only one config
     line changes.
- In Outlook, create a rule that moves emails from the banks' senders to a
  folder `Gastos`. The agent only reads that folder.
- Store each processed `internetMessageId` so you never process the same email
  twice.
- **Capture mode:** for the first 2–3 weeks the agent also saves a redacted copy
  of every email from a bank or merchant sender to `samples/` on the Pi. These
  become the parser test fixtures (see §9), so you don't have to forward emails
  by hand.

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

**Merchant apps (Copec, Rappi).** Your card is saved in both apps, so the bank
email only shows something like `COPEC APP` or `RAPPI*...`. That isn't
enough on its own: a Rappi charge can be a restaurant, a supermarket
(Turbo/Market), or a pharmacy, and a Copec app charge can be fuel or a Pronto store.
- Add **receipt parsers** for the confirmation emails Rappi and Copec send, which
  arrive at the same address as your app account. These parsers don't create
  transactions. They **enrich** an existing bank transaction, matched by amount
  (± tip or rounding) and a time window of about 30 minutes. They add the real store
  and the items, which is usually enough to classify the purchase with no question.
  Rappi charges can be delayed, or split when the order changes, so the matcher
  needs to accept small differences.
- If the app account uses another email address, either forward those receipts
  or point the app account to the same mailbox.
- Default rules until the receipt arrives: `COPEC` → Bencina (ask if
  the amount is under ~$5.000, since that's probably Pronto). `RAPPI` → wait 30 minutes
  for the receipt, then ask.

**Transfers count as expenses.** Outgoing *transferencias* (to pay rent, split a
bill, etc.) are recorded like purchases, but the bank email only names the
recipient. The first transfer to a new recipient (name + RUT/account, hashed)
always triggers a question: "Transferiste $250.000 a J. Pérez — ¿qué es?" Your
answer (for example "Arriendo") is remembered for that recipient. Transfers between
your own accounts (Banco de Chile ↔ BICE) are detected automatically and
marked as ignored. Paying your credit card bill is also ignored, because those
purchases were already counted when you made them.

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
You're paid on the 28th, **or on the previous business day** when the 28th falls
on a weekend or a Chilean holiday. Each period runs from one actual payday to the
day before the next one.

```python
CL_HOLIDAYS = holidays.country_holidays("CL")

def actual_payday(year: int, month: int, payday: int = 28) -> date:
    d = date(year, month, min(payday, monthrange(year, month)[1]))
    while d.weekday() >= 5 or d in CL_HOLIDAYS:   # Sat/Sun or holiday → go back
        d -= timedelta(days=1)
    return d

def period_for(d: date, payday: int = 28) -> tuple[date, date]:
    start = actual_payday(d.year, d.month, payday)
    if d < start:                                   # still in last month's period
        prev = d.replace(day=1) - timedelta(days=1)
        start = actual_payday(prev.year, prev.month, payday)
    nxt = start.replace(day=1) + relativedelta(months=1)
    end = actual_payday(nxt.year, nxt.month, payday) - timedelta(days=1)
    return start, end
```
Example: 28 Feb 2027 is a Sunday, so that period starts on Friday 26 Feb 2027 and
the previous one ends on 25 Feb.

- Make `payday` a config setting (it's 28 now but could change). Store each period's
  start date on every transaction, so changing the payday doesn't rewrite old periods.
- Add a `/pago` command to override a period's start if you're paid on an
  unexpected date.
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
- **Getting the file off the Pi:** use `/excel` on Telegram, which sends the
  current `.xlsx`. You can also share the folder over the home network with Samba
  (`\\raspberrypi\gastos`). The agent writes to a temporary file and renames it
  into place, so a copy you have open in Excel is never corrupted. Excel never
  writes back: edits go through Telegram, or a future web UI.

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

## 5. The Pi as a host for many agents

The Raspberry Pi will host several agents, and this expense tracker is the first.
The repo is laid out as a small **platform**: shared code in `pi_core`, with one
package per agent.

- **`pi_core` (shared):** config loading, logging, SQLite helpers, a scheduler, a
  Telegram wrapper (with the `chat_id` whitelist), an LLM client, and a `MailSource`
  abstraction. A future agent such as "package tracking" or "bill due dates"
  reuses all of it.
- **One process per agent, managed by `systemd`:** `pi-agent@expenses.service`,
  `pi-agent@<next>.service`, with `Restart=always`. If one agent crashes the
  others keep running, and you can update each one on its own schedule. Docker
  Compose is an alternative if you'd rather have containers. systemd is lighter
  on a Pi and is the default here.
- **Telegram:** each agent gets its own bot from @BotFather (free and instant), for
  example `@juan_gastos_bot`. Each agent then has its own clean chat, and you
  don't need a router. If that gets messy later, you can add a single "hub" bot
  that dispatches by command prefix.
- **Pi setup:**
  - Raspberry Pi OS Lite 64-bit and Python 3.12 via `uv`.
  - Boot from an SSD over USB rather than the SD card. SQLite writes wear out SD
    cards.
  - Set the time zone with `timedatectl set-timezone America/Santiago` and keep
    NTP on, so purchase times and period boundaries are correct.
  - Nightly backup of `/var/lib/pi-agents/*/` to another machine or cloud storage.
  - Use Tailscale for remote SSH. Nothing needs to be exposed to the internet,
    because Telegram uses long polling and Microsoft Graph is outbound only.
- **Resources:** each agent is mostly idle (polling every 60 s). A Pi 4 or 5 can run
  many of them. The LLM runs in the cloud, so the Pi does no heavy work.

---

## 6. Project structure

```
Pi-Ai-Agent/
├── pyproject.toml                 # uv workspace, Python 3.12
├── .env.example                   # per-agent secrets template
├── packages/
│   └── pi_core/                   # shared by all agents
│       ├── config.py              # pydantic-settings
│       ├── db.py                  # SQLite helpers
│       ├── telegram.py            # bot wrapper + chat_id whitelist
│       ├── llm.py                 # anthropic client wrapper
│       └── mail/
│           ├── base.py            # MailSource interface
│           ├── graph.py           # Microsoft Graph (msal device-code, delta query)
│           └── imap.py            # IMAP alternative (imap-tools)
├── agents/
│   └── expenses/
│       ├── config.yaml            # payday, banks, threshold, paths
│       ├── categories.yaml        # categories + keyword rules
│       └── src/expenses/
│           ├── main.py            # asyncio: poller + bot + scheduler
│           ├── models.py          # Transaction dataclass
│           ├── periods.py         # pay period logic (business-day aware)
│           ├── parsers/
│           │   ├── base.py
│           │   ├── bancochile.py  # purchases, transfers
│           │   ├── bice.py        # purchases, transfers
│           │   ├── rappi.py       # receipt enricher
│           │   ├── copec.py       # receipt enricher
│           │   └── llm_fallback.py
│           ├── classify/          # memory.py, rules.py, llm.py
│           ├── bot/               # handlers.py, notifications.py
│           ├── export/excel.py
│           └── reconcile/cartola.py
├── tests/
│   ├── fixtures/emails/           # redacted .eml samples per bank/merchant
│   └── ...
├── tools/
│   └── redact_eml.py              # strip personal data from sample emails
└── deploy/
    └── pi-agent@.service          # systemd template unit
```

**Main libraries:** `msal` + `httpx` (Microsoft Graph), `imap-tools`,
`beautifulsoup4`, `python-telegram-bot`, `anthropic`, `openpyxl`,
`pydantic-settings`, `python-dateutil`, `holidays`, `APScheduler` (or the job queue
built into PTB), `pytest`.

---

## 7. Milestones

| # | Milestone | Done when |
|---|---|---|
| 0 | **Setup.** Turn on "notify every purchase/transfer" in Banco de Chile and BICE. Create the Outlook rule that moves bank emails to `Gastos`. Install Pi OS. Create the Telegram bot. | Bank emails land in `Gastos` |
| 1 | **`pi_core` + Graph mail source + capture mode.** Device-code login on the Pi, and redacted samples saved to `samples/`. | Graph login works with `@uc.cl` (or you've switched to the IMAP fallback) and samples are accumulating |
| 2 | **Parsing core:** `models`, `periods`, Banco de Chile and BICE parsers (purchases + transfers), tests | `pytest` passes against real fixtures |
| 3 | **SQLite + dedup + Excel export** | Last month's purchases are in `gastos.xlsx` with no duplicates |
| 4 | **Telegram bot:** notifications, classification questions, `/resumen`, `/gasto`, `/excel` | You can classify a purchase from your phone |
| 5 | **Classifier** (memory, then rules, then LLM) plus **Rappi/Copec enrichment** | Fewer than 20% of purchases need a question after 2 weeks |
| 6 | **Deploy** as `pi-agent@expenses.service`, with a health check ("I haven't seen a bank email in 3 days", "Graph token expired") | Runs unattended for one full period |
| 7 | **Extras:** *cartola* reconciliation, budgets, USD conversion, next agent | As needed |

You can build and test milestones 2–3 on your laptop before the Pi is ready.

**Status:** milestone 1 code is done: `pi_core` (Graph and IMAP sources, redaction, SQLite helpers), capture mode, pay periods, and the systemd unit, all with tests. The next step is the setup in [docs/SETUP.md](docs/SETUP.md).

---

## 8. Security and privacy
- Use read-only mail permissions (`Mail.Read`). Keep the token cache and `.env` out of git, with `chmod 600`.
- Only your `chat_id` can talk to the bot.
- The LLM receives only the merchant string and amount, never the full email
  (except in the fallback extractor, which strips names, RUT, and account numbers first).
  For maximum privacy you could run a local model, at the cost of accuracy and
  speed on a Pi.
- Back up `expenses.db` (for example a nightly copy to Google Drive or another machine).

---

## 9. Getting sample emails (parser fixtures)

The parsers need real examples of each email format. There are three ways to provide them:

1. **Do nothing and let capture mode collect them (recommended).** Once milestone 1
   runs on the Pi, it saves every bank and merchant email as a redacted `.eml` file
   in `samples/`. Commit them to `tests/fixtures/emails/`, or attach them to a
   Claude session, and the parsers get written against them.
2. **Export them by hand from Outlook on the web:** open the email, then
   **⋯ → Save / Download** (the menu label varies by Outlook version) to get an
   `.eml` file. Run `python tools/redact_eml.py file.eml` and attach the
   `*.redacted.eml` output to a chat with Claude, or commit it to the repo.
   Aim for 3–5 of each: a Banco de Chile purchase, a Banco de Chile transfer, a BICE
   purchase, a BICE transfer, a Rappi receipt, and a Copec receipt. Include one in
   *cuotas* and one in USD if you have them.
3. **Quick but less precise:** in Outlook choose **⋯ → View → View message source**,
   copy it, and paste it into the chat. Remove your name, RUT, and account numbers
   first.

`tools/redact_eml.py` replaces RUTs, full card/account numbers (it keeps the last 4
digits), your name, and your email address with placeholders. It keeps everything the
parsers need: amounts, merchants, dates, and the HTML structure.

---

## 10. Decisions

| Question | Decision |
|---|---|
| Email provider | A **dedicated Gmail account over IMAP** (app password). The banks, Rappi and Copec send to it. UC's Microsoft 365 doesn't let users register apps, so the Graph route stays as an unused option. |
| Host | Raspberry Pi, as a multi-agent host with systemd units |
| Banks | Banco de Chile and BICE, plus Rappi and Copec receipt enrichment |
| Output | A local Excel file on the Pi, available through `/excel` and a Samba share |
| Payday on a weekend/holiday | Paid the previous business day, so the period starts that day |
| Transfers to others | Counted as expenses, with the category remembered per recipient |
