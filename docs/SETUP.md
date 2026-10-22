# Setup: expenses agent (milestone 1, capture mode)

At this stage the agent reads your bank emails and saves a redacted copy of each
one, so the parsers can be written against real examples. It doesn't record
expenses yet.

## 1. Banks: turn on notifications
In the Banco de Chile and BICE apps or websites, turn on email notifications for
**every** card purchase and every outgoing transfer, with no minimum amount.

## 2. Outlook: send bank emails to a `Gastos` folder
In Outlook on the web (outlook.office.com):
1. Create a folder named `Gastos`, either at the top level or inside the Inbox.
2. Go to **Settings → Mail → Rules → Add new rule**.
   - Condition: **From** contains `bancochile.cl`, `bice.cl`, `rappi`, `copec`.
   - Action: **Move to** `Gastos`.
3. Optional: use **Run rule now** to move older emails. The first sync reads
   everything already in the folder, which produces extra samples.

The agent only ever reads this folder, with read-only access.

## 3. Register an app in Microsoft Entra (free, ~5 min)
The Pi needs a "client ID" to sign in to Microsoft Graph as you.

1. Go to https://entra.microsoft.com and sign in with `juan.lorca@uc.cl`.
2. Open **Applications → App registrations → New registration**:
   - Name: `Pi expenses agent`
   - Supported account types: **Accounts in any organizational directory and
     personal Microsoft accounts**. With this choice the same app keeps working
     if you later move to an Outlook.com mailbox.
   - Redirect URI: leave empty.
3. Under **Authentication**, set **Allow public client flows** to **Yes** and save.
   The device-code login needs this.
4. Under **API permissions**, choose **Add a permission → Microsoft Graph → Delegated** and
   add `Mail.Read`. `User.Read` is already there.
5. Copy the **Application (client) ID** from **Overview**.

**If UC blocks this:**
- **"You don't have permission to register applications":** sign in to
  entra.microsoft.com with a personal Microsoft account and register the app there,
  with the same account type as in step 2. Microsoft may ask you to create a free
  directory first.
- **At login (step 5), "Need admin approval" / "Approval required":** UC doesn't let
  users approve third-party apps. Use the IMAP fallback (section 7).

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
#   set mail.graph.client_id, and add: data_dir: /var/lib/pi-agents/expenses
echo 'EXPENSES_CONFIG=/etc/pi-agents/expenses.yaml' | sudo tee /etc/pi-agents/expenses.env
sudo chmod 640 /etc/pi-agents/*; sudo chgrp piagent /etc/pi-agents/*

sudo install -d -o piagent -g piagent -m 700 /var/lib/pi-agents/expenses
sudo cp /opt/pi-ai-agent/deploy/pi-agent@.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## 5. Log in once and test
```bash
cd /opt/pi-ai-agent
sudo -u piagent EXPENSES_CONFIG=/etc/pi-agents/expenses.yaml .venv/bin/python -m expenses login
#  -> "To sign in, use a web browser to open https://microsoft.com/devicelogin and enter the code XXXXXXX"
#     Open it on your phone, enter the code, and sign in with juan.lorca@uc.cl

sudo -u piagent EXPENSES_CONFIG=/etc/pi-agents/expenses.yaml .venv/bin/python -m expenses fetch-once
#  -> "N new message(s). Samples in /var/lib/pi-agents/expenses/samples"

sudo systemctl enable --now pi-agent@expenses
journalctl -u pi-agent@expenses -f          # watch it work
```
If the logs say `Mail login needed`, run the `login` command again. The refresh
token lasts for months, but UC can revoke it, for example after a password change.

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

## 7. Fallback: dedicated mailbox over IMAP
Use this if UC blocks the Graph login, or when you leave UC.
1. Create a new Gmail account just for this. Turn on 2-step verification, then
   create an **app password** at https://myaccount.google.com/apppasswords.
2. In Banco de Chile and BICE, change your notification email to the new address.
   Do the same for the Rappi and Copec accounts, or forward their receipts.
3. In Gmail, create a filter that applies the label `Gastos` to those senders.
4. In `/etc/pi-agents/expenses.yaml`:
   ```yaml
   mail:
     source: imap
     imap: {host: imap.gmail.com, user: you.gastos@gmail.com, folder: Gastos}
   ```
   Add `IMAP_PASSWORD=<app password>` to `/etc/pi-agents/expenses.env`.
5. Restart the service with `sudo systemctl restart pi-agent@expenses`.
