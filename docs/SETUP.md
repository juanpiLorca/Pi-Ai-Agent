# Setup: expenses agent (milestone 1, capture mode)

At this stage the agent reads your bank emails and saves a redacted copy of each
one, so the parsers can be written against real examples. It doesn't record
expenses yet.

The bank emails go to a **dedicated Gmail account** that the agent reads over
IMAP with an app password. (The UC Microsoft 365 account doesn't let users
register apps. See the appendix if you ever want the Outlook/Graph route.)

## 1. Create the dedicated Gmail account
1. Create a new Gmail account only for this, e.g. `juan.gastos@gmail.com`. If
   the agent's password ever leaks, the exposure is limited to bank notifications.
2. Turn on **2-Step Verification** at https://myaccount.google.com/security. App
   passwords require it.
3. Create an **app password** at https://myaccount.google.com/apppasswords (name it
   `pi-expenses`). Copy the 16-character code. This is what the agent uses, never your
   real password, and you can revoke it at any time.
4. In Gmail, go to **Settings → See all settings → Filters and Blocked Addresses →
   Create a new filter**:
   - From: `bancochile.cl OR bice.cl OR rappi OR copec`
   - Action: **Apply the label** `Gastos` (create it). Optionally also **Never send it to Spam**.

## 2. Point the banks and apps at the new address
- **Banco de Chile** and **BICE:** in the app or website, open your profile or contact
  details and change the email address. Then turn on email notifications for
  **every** card purchase and outgoing transfer, with no minimum amount. The
  bank may ask for your second factor (digital key / Digipass).
  Note: some banks use one email for everything, so statements and security alerts
  will also go to the new account. That's fine, but check it every now and then
  or have Gmail forward non-`Gastos` emails to your personal address.
- **Rappi** and **Copec:** change the account email in each app, so receipts
  arrive in the same mailbox. They're used later to identify what you bought.

Tip: make a small purchase afterwards and confirm the email shows up under `Gastos`.

## 3. Optional: test on your laptop first
You need `git`.

```bash
# Install uv (it brings its own Python)
#   macOS/Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh
#   Windows (PowerShell):  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone -b claude/chile-expense-tracker-qe08ok https://github.com/juanpiLorca/Pi-Ai-Agent.git
cd Pi-Ai-Agent
uv sync
cp agents/expenses/config.example.yaml agents/expenses/config.yaml   # Windows: copy ...
# Edit agents/expenses/config.yaml: set mail.imap.user to the new Gmail address

# Give the app password to the agent for this terminal session:
export IMAP_PASSWORD='abcd efgh ijkl mnop'          # PowerShell: $env:IMAP_PASSWORD='abcd efgh ijkl mnop'
uv run python -m expenses fetch-once   # -> "N new message(s). Samples in data/samples"
```
Everything stays in the repo's `data/` folder, which git ignores. Open a file in
`data/samples/` to check the redaction. Delete `data/` once you move to the Pi.

## 4. Install on the Raspberry Pi
Use Raspberry Pi OS Lite 64-bit, preferably booting from a USB SSD.

```bash
sudo timedatectl set-timezone America/Santiago
sudo useradd --system --create-home piagent
sudo install -d -o piagent -g piagent /opt/pi-ai-agent
sudo -u piagent git clone https://github.com/juanpiLorca/Pi-Ai-Agent.git /opt/pi-ai-agent
sudo -u piagent sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
# Use the system Python (3.11+). A uv-managed Python would live under /home, which the
# service can't see (ProtectHome=true).
sudo -u piagent sh -c 'cd /opt/pi-ai-agent && ~/.local/bin/uv sync --frozen --no-dev --python-preference only-system'

sudo mkdir -p /etc/pi-agents
sudo cp /opt/pi-ai-agent/agents/expenses/config.example.yaml /etc/pi-agents/expenses.yaml
sudo nano /etc/pi-agents/expenses.yaml
#   set mail.imap.user, and add the line:  data_dir: /var/lib/pi-agents/expenses
sudo tee /etc/pi-agents/expenses.env > /dev/null <<'EOF'
EXPENSES_CONFIG=/etc/pi-agents/expenses.yaml
IMAP_PASSWORD=abcdefghijklmnop
EOF
# write the app password without spaces (Gmail accepts it either way)
sudo chgrp piagent /etc/pi-agents/*; sudo chmod 640 /etc/pi-agents/*

sudo install -d -o piagent -g piagent -m 700 /var/lib/pi-agents/expenses
sudo cp /opt/pi-ai-agent/deploy/pi-agent@.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## 5. Test and start
```bash
cd /opt/pi-ai-agent
sudo -u piagent sh -c 'set -a; . /etc/pi-agents/expenses.env; .venv/bin/python -m expenses fetch-once'
#  -> "N new message(s). Samples in /var/lib/pi-agents/expenses/samples"

sudo systemctl enable --now pi-agent@expenses
journalctl -u pi-agent@expenses -f          # watch it work
```
If the logs show a login error, the app password was revoked or mistyped. Create
a new one and update `/etc/pi-agents/expenses.env`.

## 6. Share the samples
After 2–3 weeks:
```bash
sudo tar czf ~/samples.tgz -C /var/lib/pi-agents/expenses samples
scp pi@raspberrypi:samples.tgz .
```
Open a few files to check the redaction: your name, RUT, and full card or
account numbers should all be gone. Then either attach them to a Claude
session, or commit them under `agents/expenses/tests/fixtures/emails/`.
Only commit them if the repository is private: they still show what you buy
and where.

---

## Appendix: Outlook via Microsoft Graph
Only needed if you want to read an Outlook mailbox where you can register an app:
your own Microsoft 365 tenant, or Outlook.com through a personal Entra directory.
UC accounts can't do this.

1. Go to https://entra.microsoft.com, then **Applications → App registrations → New
   registration**, with account type **Accounts in any organizational directory and
   personal Microsoft accounts**.
2. Under **Authentication**, set **Allow public client flows** to **Yes**.
3. Under **API permissions**, choose **Microsoft Graph → Delegated** and add `Mail.Read`.
4. In the config, set `mail.source: graph` and fill in the `graph:` section with the
   Application (client) ID. Use `consumers` as the authority for Outlook.com.
5. Run `python -m expenses login` once and enter the code at microsoft.com/devicelogin.
