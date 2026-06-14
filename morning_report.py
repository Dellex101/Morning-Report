#!/usr/bin/env python3
"""
Morning stock briefing -- MU & SNDK
===================================
Pulls overnight price action, a front-month options / implied-volatility
snapshot, the next earnings date, and the latest headlines for each ticker,
then emails you a clean HTML briefing.

Built to run unattended on a schedule -- GitHub Actions cron in the cloud,
or a local cron job on your own machine.

Usage
-----
    python morning_report.py          # build the report and email it
    python morning_report.py --demo   # build a SAMPLE with placeholder data
                                       # (no network, no email); writes
                                       # morning_report_demo.html so you can
                                       # preview the layout
"""

import os
import sys
import html as html_lib
import smtplib
import urllib.parse
import datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

# ----------------------------------------------------------------------
# Configuration -- change tickers here if you like
# ----------------------------------------------------------------------
TICKERS = ["MU", "SNDK"]
COMPANY = {"MU": "Micron Technology", "SNDK": "SanDisk"}
TZ = ZoneInfo("America/Toronto")
NEWS_PER_TICKER = 5

# Email credentials are read from environment variables (GitHub Secrets or
# your shell). Nothing sensitive is hard-coded into this file.
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")          # e.g. you@gmail.com
SMTP_PASS = os.environ.get("SMTP_PASS")          # Gmail App Password
EMAIL_TO = os.environ.get("EMAIL_TO") or SMTP_USER

POS = "#15803d"   # green for gains
NEG = "#b91c1c"   # red for losses


# ----------------------------------------------------------------------
# Small formatting helpers (all None-safe)
# ----------------------------------------------------------------------
def esc(s):
    return html_lib.escape(str(s)) if s is not None else ""


def fmt_money(x):
    return f"${x:,.2f}" if isinstance(x, (int, float)) else "n/a"


def fmt_pct(x):
    return f"{x:+.2f}%" if isinstance(x, (int, float)) else "n/a"


def human_count(n):
    if not isinstance(n, (int, float)):
        return "n/a"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.2f}{unit}"
    return f"{n:,.0f}"


def human_big(n):
    if not isinstance(n, (int, float)):
        return "n/a"
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(n) >= div:
            return f"${n / div:.2f}{unit}"
    return f"${n:,.0f}"


def fmt_date(d):
    if d is None:
        return "n/a"
    if isinstance(d, (dt.date, dt.datetime)):
        return d.strftime("%b %d, %Y")
    return str(d)


# ----------------------------------------------------------------------
# Data fetchers (network) -- each is wrapped defensively by fetch_all
# ----------------------------------------------------------------------
def fetch_quote(t):
    """Price metrics via yfinance fast_info (the reliable, lightweight path)."""
    fi = t.fast_info

    def g(attr):
        try:
            return getattr(fi, attr)
        except Exception:
            try:
                return fi[attr]
            except Exception:
                return None

    last = g("last_price")
    prev = g("previous_close")
    change = (last - prev) if (last is not None and prev is not None) else None
    pct = (change / prev * 100) if (change is not None and prev) else None
    return {
        "last": last,
        "prev": prev,
        "change": change,
        "pct": pct,
        "day_low": g("day_low"),
        "day_high": g("day_high"),
        "year_low": g("year_low"),
        "year_high": g("year_high"),
        "volume": g("last_volume"),
        "avg_volume": g("three_month_average_volume"),
        "market_cap": g("market_cap"),
    }


def fetch_iv(t, spot):
    """Front-month expiry and average at-the-money implied volatility (%)."""
    try:
        exps = t.options
        if not exps:
            return None, None
        expiry = exps[0]
        chain = t.option_chain(expiry)
        ivs = []
        for df in (chain.calls, chain.puts):
            if df is None or df.empty or spot is None:
                continue
            idx = (df["strike"] - spot).abs().idxmin()
            iv = df.loc[idx, "impliedVolatility"]
            if iv and iv > 0:
                ivs.append(float(iv))
        atm_iv = (sum(ivs) / len(ivs) * 100) if ivs else None
        return expiry, atm_iv
    except Exception:
        return None, None


def fetch_earnings(t):
    """Next earnings date, handling both old and new yfinance shapes."""
    try:
        cal = t.calendar
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed:
                return ed[0] if isinstance(ed, (list, tuple)) else ed
        else:  # older versions return a DataFrame
            return cal.loc["Earnings Date"][0]
    except Exception:
        pass
    return None


def fetch_news(company, feedparser, n=NEWS_PER_TICKER):
    """Latest headlines from Google News RSS (no API key needed)."""
    q = urllib.parse.quote(f"{company} stock")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    out = []
    try:
        feed = feedparser.parse(url)
        for e in feed.entries[:n]:
            src = e.get("source", {}) or {}
            out.append({
                "title": e.get("title", "(no title)"),
                "link": e.get("link", "#"),
                "source": src.get("title", "") if isinstance(src, dict) else "",
                "published": e.get("published", ""),
            })
    except Exception:
        pass
    return out


def fetch_all(ticker, yf, feedparser):
    company = COMPANY.get(ticker, ticker)
    report = {"ticker": ticker, "company": company, "news": [], "error": None}
    try:
        t = yf.Ticker(ticker)
        report.update(fetch_quote(t))
        expiry, atm_iv = fetch_iv(t, report.get("last"))
        report["iv_expiry"] = expiry
        report["atm_iv"] = atm_iv
        report["earnings"] = fetch_earnings(t)
        report["news"] = fetch_news(company, feedparser)
    except Exception as exc:  # one ticker failing must not kill the report
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------
def _metric_row(label, value):
    return (
        f'<tr>'
        f'<td style="padding:3px 0;font-size:13px;color:#6b7280;">{label}</td>'
        f'<td style="padding:3px 0;font-size:14px;color:#111827;text-align:right;'
        f'font-weight:600;">{value}</td>'
        f'</tr>'
    )


def _card(r):
    parts = []
    parts.append(
        '<div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;'
        'padding:18px 20px;margin:0 0 16px 0;">'
    )

    if r.get("error"):
        parts.append(
            f'<div style="font-size:16px;font-weight:700;color:#111827;">'
            f'{esc(r["ticker"])} &mdash; {esc(r["company"])}</div>'
            f'<div style="margin-top:8px;font-size:13px;color:{NEG};">'
            f'Could not load data: {esc(r["error"])}</div></div>'
        )
        return "".join(parts)

    pct = r.get("pct")
    up = isinstance(pct, (int, float)) and pct >= 0
    color = POS if up else NEG
    arrow = "&#9650;" if up else "&#9660;"
    chg_money = fmt_money(abs(r["change"])) if isinstance(r.get("change"), (int, float)) else "n/a"

    # Header: ticker, company, last price, change badge
    parts.append(
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;">'
        f'<div style="font-size:16px;font-weight:700;color:#111827;">'
        f'{esc(r["ticker"])} <span style="font-weight:500;color:#6b7280;font-size:14px;">'
        f'{esc(r["company"])}</span></div>'
        f'<div style="font-size:18px;font-weight:700;color:#111827;">{fmt_money(r.get("last"))}</div>'
        f'</div>'
        f'<div style="margin:2px 0 12px;font-size:14px;color:{color};font-weight:700;">'
        f'{arrow} {chg_money} ({fmt_pct(pct)})</div>'
    )

    # Options snapshot (highlighted -- relevant for options traders)
    iv_txt = f'{r["atm_iv"]:.1f}%' if isinstance(r.get("atm_iv"), (int, float)) else "n/a"
    parts.append(
        f'<div style="background:#f0f7ff;border:1px solid #dbeafe;border-radius:8px;'
        f'padding:8px 12px;margin-bottom:12px;font-size:13px;color:#1e3a8a;">'
        f'<b>Options:</b> ATM IV {iv_txt} &nbsp;&middot;&nbsp; '
        f'front-month exp {fmt_date(r.get("iv_expiry"))} &nbsp;&middot;&nbsp; '
        f'next earnings {fmt_date(r.get("earnings"))}</div>'
    )

    # Metrics table
    vol = human_count(r.get("volume"))
    avg = human_count(r.get("avg_volume"))
    parts.append('<table style="width:100%;border-collapse:collapse;">')
    parts.append(_metric_row("Prev close", fmt_money(r.get("prev"))))
    parts.append(_metric_row(
        "Day range",
        f'{fmt_money(r.get("day_low"))} &ndash; {fmt_money(r.get("day_high"))}'))
    parts.append(_metric_row(
        "52-week range",
        f'{fmt_money(r.get("year_low"))} &ndash; {fmt_money(r.get("year_high"))}'))
    parts.append(_metric_row("Volume (vs 3m avg)", f'{vol} &nbsp;/&nbsp; {avg}'))
    parts.append(_metric_row("Market cap", human_big(r.get("market_cap"))))
    parts.append('</table>')

    # News
    parts.append(
        '<div style="margin-top:14px;font-size:12px;font-weight:700;color:#6b7280;'
        'text-transform:uppercase;letter-spacing:.04em;">Latest headlines</div>'
    )
    if r["news"]:
        for item in r["news"]:
            parts.append(
                f'<div style="margin:8px 0;">'
                f'<a href="{esc(item["link"])}" '
                f'style="color:#1d4ed8;text-decoration:none;font-size:14px;font-weight:600;">'
                f'{esc(item["title"])}</a>'
                f'<div style="font-size:12px;color:#9ca3af;">'
                f'{esc(item["source"])}{" &middot; " if item["source"] else ""}'
                f'{esc(item["published"])}</div></div>'
            )
    else:
        parts.append('<div style="font-size:13px;color:#9ca3af;margin-top:6px;">'
                     'No headlines retrieved.</div>')

    parts.append('</div>')
    return "".join(parts)


def build_html(reports, demo=False):
    now = dt.datetime.now(TZ)
    datestr = now.strftime("%A, %B %d, %Y")
    hour = now.strftime("%I").lstrip("0") or "12"
    timestr = f"{hour}:{now.strftime('%M %p %Z')}"

    parts = [
        '<div style="background:#f4f5f7;padding:24px 12px;font-family:-apple-system,'
        'Segoe UI,Roboto,Helvetica,Arial,sans-serif;">',
        '<div style="max-width:640px;margin:0 auto;">',
    ]
    if demo:
        parts.append(
            '<div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:8px;'
            'padding:10px 14px;margin-bottom:16px;font-size:13px;color:#92400e;">'
            '<b>SAMPLE / PREVIEW</b> &mdash; placeholder figures, not live quotes. '
            'This is just to show the layout.</div>'
        )
    parts.append(
        f'<div style="margin-bottom:18px;">'
        f'<div style="font-size:22px;font-weight:800;color:#111827;">Morning Briefing</div>'
        f'<div style="font-size:14px;color:#6b7280;">{datestr} &middot; generated {timestr}</div>'
        f'</div>'
    )
    for r in reports:
        parts.append(_card(r))
    parts.append(
        '<div style="font-size:11px;color:#9ca3af;margin-top:8px;line-height:1.5;">'
        'Data: Yahoo Finance (price &amp; options) and Google News (headlines). '
        'For information only &mdash; not investment advice. IV shown is the average '
        'at-the-money implied volatility of the nearest expiry.</div>'
    )
    parts.append('</div></div>')
    return "".join(parts)


def build_text(reports):
    now = dt.datetime.now(TZ)
    lines = [f"MORNING BRIEFING -- {now.strftime('%A, %B %d, %Y')}", ""]
    for r in reports:
        lines.append(f"{r['ticker']}  {r['company']}")
        if r.get("error"):
            lines.append(f"  Could not load data: {r['error']}")
            lines.append("")
            continue
        iv_txt = f"{r['atm_iv']:.1f}%" if isinstance(r.get("atm_iv"), (int, float)) else "n/a"
        chg = fmt_money(abs(r["change"])) if isinstance(r.get("change"), (int, float)) else "n/a"
        lines.append(
            f"  Last {fmt_money(r.get('last'))} ({fmt_pct(r.get('pct'))}, {chg}) | "
            f"ATM IV {iv_txt} exp {fmt_date(r.get('iv_expiry'))} | "
            f"earnings {fmt_date(r.get('earnings'))}"
        )
        lines.append(
            f"  Range {fmt_money(r.get('day_low'))}-{fmt_money(r.get('day_high'))} | "
            f"52wk {fmt_money(r.get('year_low'))}-{fmt_money(r.get('year_high'))} | "
            f"Vol {human_count(r.get('volume'))}"
        )
        for item in r["news"]:
            lines.append(f"  - {item['title']} ({item['source']})")
            lines.append(f"    {item['link']}")
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Email
# ----------------------------------------------------------------------
def send_email(text_body, html_body):
    now = dt.datetime.now(TZ)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Morning Briefing: {', '.join(TICKERS)} -- {now.strftime('%b %d')}"
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [EMAIL_TO], msg.as_string())


# ----------------------------------------------------------------------
# Demo data (for --demo preview only)
# ----------------------------------------------------------------------
def demo_reports():
    return [
        {
            "ticker": "MU", "company": "Micron Technology", "error": None,
            "last": 110.25, "prev": 106.80, "change": 3.45, "pct": 3.23,
            "day_low": 107.10, "day_high": 111.40,
            "year_low": 61.50, "year_high": 157.50,
            "volume": 28_500_000, "avg_volume": 31_200_000,
            "market_cap": 122_000_000_000,
            "iv_expiry": "2026-01-16", "atm_iv": 48.5, "earnings": "2026-03-19",
            "news": [
                {"title": "Sample headline: memory pricing trends in focus",
                 "link": "https://example.com/1", "source": "Sample Source",
                 "published": "Sat, 14 Jun 2026 12:00:00 GMT"},
                {"title": "Sample headline: analysts preview the next quarter",
                 "link": "https://example.com/2", "source": "Sample Source",
                 "published": "Sat, 14 Jun 2026 09:30:00 GMT"},
            ],
        },
        {
            "ticker": "SNDK", "company": "SanDisk", "error": None,
            "last": 42.10, "prev": 43.05, "change": -0.95, "pct": -2.21,
            "day_low": 41.60, "day_high": 43.20,
            "year_low": 28.90, "year_high": 58.40,
            "volume": 9_800_000, "avg_volume": 12_400_000,
            "market_cap": 6_100_000_000,
            "iv_expiry": "2026-01-16", "atm_iv": 61.2, "earnings": "2026-01-29",
            "news": [
                {"title": "Sample headline: flash storage demand commentary",
                 "link": "https://example.com/3", "source": "Sample Source",
                 "published": "Sat, 14 Jun 2026 11:15:00 GMT"},
            ],
        },
    ]


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def main():
    if "--demo" in sys.argv:
        html = build_html(demo_reports(), demo=True)
        with open("morning_report_demo.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Wrote morning_report_demo.html")
        return

    # Real run -- import the heavy deps only when actually needed
    import yfinance as yf
    import feedparser

    reports = [fetch_all(tk, yf, feedparser) for tk in TICKERS]
    html = build_html(reports)
    text = build_text(reports)

    if not (SMTP_USER and SMTP_PASS and EMAIL_TO):
        print("Email environment variables are missing; printing the report instead.\n")
        print(text)
        return

    send_email(text, html)
    print(f"Report sent to {EMAIL_TO}")


if __name__ == "__main__":
    main()
