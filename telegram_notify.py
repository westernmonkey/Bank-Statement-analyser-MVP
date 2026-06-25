"""Telegram notifications for completed fundability analyses."""

import html
import os
from datetime import datetime

import httpx

TELEGRAM_MAX_LENGTH = 4096


def _esc(value) -> str:
    return html.escape(str(value if value is not None else "—"), quote=False)


def _fmt_metric(val, suffix: str = "") -> str:
    if val is None:
        return "—"
    if suffix == "AED" and isinstance(val, (int, float)):
        if val >= 1_000_000:
            return f"AED {val / 1_000_000:.2f}M"
        if val >= 1_000:
            return f"AED {val / 1_000:.1f}K"
        return f"AED {val:,.0f}"
    if suffix == "%" and isinstance(val, (int, float)):
        return f"{val}%"
    return str(val)


def _score_bar(score: int, blocks: int = 10) -> str:
    score = max(0, min(100, int(score or 0)))
    filled = round(score / 100 * blocks)
    return "🟩" * filled + "⬜" * (blocks - filled)


def _verdict_emoji(verdict: str) -> str:
    v = (verdict or "").lower()
    if "strong" in v:
        return "🟢"
    if "moderate" in v:
        return "🟡"
    return "🔴"


def _short_time(ts: str) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y · %H:%M UTC")
    except ValueError:
        return ts[:16]


def format_analysis_telegram(record: dict) -> str:
    """Mobile-friendly HTML message for Telegram (OnePlus / narrow screens)."""
    cp = record.get("company_profile") or {}
    metrics = record.get("metrics") or {}
    score = int(record.get("score") or 0)
    verdict = record.get("verdict") or "—"
    lead_name = (record.get("lead_name") or "").strip() or "—"
    lead_phone = (record.get("lead_phone") or "").strip() or "—"
    company = (cp.get("name") or "").strip() or "—"
    address = ", ".join(cp.get("address_lines") or []) or ""

    metric_lines = [
        ("Avg monthly revenue", _fmt_metric(metrics.get("avg_monthly_revenue"), "AED")),
        ("Closing balance", _fmt_metric(metrics.get("closing_balance"), "AED")),
        ("Opening balance", _fmt_metric(metrics.get("opening_balance"), "AED")),
        ("Total credits", _fmt_metric(metrics.get("total_credits_6m"), "AED")),
        ("Total debits", _fmt_metric(metrics.get("total_debits_6m"), "AED")),
        ("Credit txns", metrics.get("num_credit_txns", "—")),
        ("Debit txns", metrics.get("num_debit_txns", "—")),
        ("Statement period", f"{metrics.get('period_months', '—')} mo"),
        ("Cash deposit mix", _fmt_metric(metrics.get("cash_deposit_pct"), "%")),
        ("Cheque returns", metrics.get("cheque_bounces", "—")),
        ("ATM withdrawals", metrics.get("atm_withdrawals", "—")),
        ("Unique payers", metrics.get("unique_inflow_sources", "—")),
    ]

    metrics_html = "\n".join(
        f"  • <b>{_esc(label)}</b>\n    {_esc(val)}" for label, val in metric_lines
    )

    points = record.get("points") or []
    insights_html = "\n".join(
        f"  {_esc(i)}. {_esc(p)}" for i, p in enumerate(points, 1)
    ) if points else "  —"

    ts = _short_time(record.get("timestamp", ""))

    parts = [
        "📊 <b>Linkit · New Fundability Analysis</b>",
        f"<i>{_esc(ts)}</i>" if ts else "",
        "",
        "👤 <b>Lead</b>",
        f"  <b>Name</b>   {_esc(lead_name)}",
        f"  <b>Phone</b>  {_esc(lead_phone)}",
        "",
        "🏢 <b>Company</b> <i>(from statement)</i>",
        f"  {_esc(company)}",
    ]
    if address:
        parts.append(f"  {_esc(address)}")

    parts += [
        "",
        f"📈 <b>Score</b>  <code>{score}/100</code>  {_verdict_emoji(verdict)} <b>{_esc(verdict)}</b>",
        f"  {_esc(record.get('sub') or '—')}",
        f"  {_score_bar(score)}  <b>{score}%</b>",
        "",
        "🏦 <b>Statement</b>",
        f"  <b>Bank</b>     {_esc(record.get('bank_label') or '—')}",
        f"  <b>File</b>     {_esc(record.get('source_filename') or '—')}",
        f"  <b>AECB</b>     {_esc(record.get('aecb_input', '—'))}",
        f"  <b>Tenure</b>   {_esc(record.get('months_in_business_input', '—'))} months",
        "",
        "💰 <b>Key Metrics</b>",
        metrics_html,
        "",
        "🤖 <b>AI Insights</b>",
        insights_html,
        "",
        "—",
        "<i>Linkit Fundability Analyser</i>",
    ]

    return "\n".join(p for p in parts if p is not None)


def _split_message(text: str, limit: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


def send_analysis_telegram(record: dict) -> None:
    """Post analysis summary to Telegram. Never raises to callers."""
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; skipping Telegram notification")
        return

    try:
        payload = {
            "timestamp": datetime.now().astimezone().isoformat(),
            **record,
        }
        text = format_analysis_telegram(payload)
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        for i, chunk in enumerate(_split_message(text)):
            res = httpx.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if not res.is_success:
                print(f"Telegram error: {res.text}")
    except Exception as e:
        print(f"Telegram notification failed: {e}")
