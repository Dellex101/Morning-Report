# Morning Stock Report — MU & SNDK

A small, self-running task that emails you a pre-market briefing on **Micron (MU)**
and **SanDisk (SNDK)** every weekday morning. Each briefing contains:

- Last price, overnight change, previous close
- Day range, 52-week range, volume vs. 3-month average, market cap
- An **options snapshot**: front-month expiry, average at-the-money implied
  volatility, and the next earnings date
- The five latest headlines per name, with links

Data comes from Yahoo Finance (price and options) and Google News (headlines).
No paid API keys are required.

---

## What you get

| File | Purpose |
|------|---------|
| `morning_report.py` | Builds the briefing and emails it |
| `requirements.txt` | Python dependencies |
| `.github/workflows/morning-report.yml` | Runs it on a schedule in the cloud |

Want to see the layout first? Run `python morning_report.py --demo` — it writes
`morning_report_demo.html` (placeholder figures, no email) that you can open in a browser.

---

## Recommended setup — GitHub Actions (free, runs in the cloud)

This is the genuinely hands-off option. The job runs on GitHub's servers, so
your own machine does not need to be awake.

### 1. Create the repository
Put these files into a new GitHub repository, keeping the folder structure
(the workflow file must sit at `.github/workflows/morning-report.yml`).

### 2. Get a Gmail App Password
A normal Gmail password will not work for scripts. You need an **App Password**:

1. Enable 2-Step Verification on your Google account (required).
2. Go to **myaccount.google.com → Security → App passwords**.
3. Generate one, and copy the 16-character code.

(Any SMTP provider works — Outlook, Fastmail, etc. Just change `SMTP_HOST`/`SMTP_PORT`.)

### 3. Add repository secrets
In your repo: **Settings → Secrets and variables → Actions → New repository secret.**
Add these five:

| Name | Value |
|------|-------|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | your full Gmail address |
| `SMTP_PASS` | the 16-character App Password (no spaces) |
| `EMAIL_TO` | where the report should be sent (can be the same address) |

### 4. Test it
Go to the **Actions** tab → **Morning Stock Report** → **Run workflow**. Within a
minute or two you should receive the email. After that it runs automatically on
the schedule.

> **Two caveats with GitHub's free scheduler:** scheduled runs can be delayed a
> few minutes at busy times, and GitHub pauses scheduled workflows in a repo with
> no commits for 60 days (a single commit re-activates it).

---

## Alternative — run it on your own computer (cron)

If you would rather keep it local:

```bash
pip install -r requirements.txt

export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=you@gmail.com
export SMTP_PASS=your_app_password
export EMAIL_TO=you@gmail.com

python morning_report.py     # sends immediately, for testing
```

Then schedule it. On macOS / Linux, `crontab -e` and add (7:30 AM on weekdays):

```
30 7 * * 1-5  cd /path/to/stock-report && /usr/bin/python3 morning_report.py
```

Cron does not load your shell profile, so either put the `export` lines in a
small wrapper script that cron calls, or use a tool like `direnv`. On Windows,
use **Task Scheduler** with the same command. Note that a local cron only fires
while the machine is powered on and awake.

---

## Customizing

- **Different tickers:** edit `TICKERS` (and optionally `COMPANY`) near the top of
  `morning_report.py`.
- **Different send time:** change the `cron:` line in the workflow (it is in UTC),
  or your crontab entry (local time).
- **More or fewer headlines:** change `NEWS_PER_TICKER`.

---

*For information only — not investment advice.*
