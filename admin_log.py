"""Append-only admin log for fundability analyses."""

import json
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent
JSONL_PATH = LOG_DIR / "admin_analyses.jsonl"
JSON_PATH = LOG_DIR / "admin_analyses.json"
TABLE_PATH = LOG_DIR / "admin_analyses.log"


def _pad(label: str, width: int = 28) -> str:
    return (label + ":").ljust(width)


def _metrics_table_rows(metrics: dict) -> list[dict]:
    """Flat rows for JSON tabular view."""
    rows = [
        ("Avg monthly revenue", metrics.get("avg_monthly_revenue"), "AED"),
        ("Closing balance", metrics.get("closing_balance"), "AED"),
        ("Opening balance", metrics.get("opening_balance"), "AED"),
        ("Total credits (period)", metrics.get("total_credits_6m"), "AED"),
        ("Total debits (period)", metrics.get("total_debits_6m"), "AED"),
        ("Credit transactions", metrics.get("num_credit_txns"), ""),
        ("Debit transactions", metrics.get("num_debit_txns"), ""),
        ("Period (months)", metrics.get("period_months"), ""),
        ("Cash deposit mix", metrics.get("cash_deposit_pct"), "%"),
        ("Cheque returns", metrics.get("cheque_bounces"), ""),
        ("ATM withdrawals", metrics.get("atm_withdrawals"), ""),
        ("Unique payers", metrics.get("unique_inflow_sources"), ""),
    ]
    out = []
    for metric, val, suffix in rows:
        if val is None:
            display = "—"
        elif suffix == "AED" and isinstance(val, (int, float)):
            display = f"AED {val:,}"
        elif suffix == "%" and isinstance(val, (int, float)):
            display = f"{val}%"
        else:
            display = str(val)
        out.append({"metric": metric, "value": val, "display": display})
    return out


def structure_entry_for_json(entry: dict) -> dict:
    """Tabular-friendly JSON shape for admin review."""
    cp = entry.get("company_profile") or {}
    return {
        "timestamp": entry.get("timestamp"),
        "source": {
            "filename": entry.get("source_filename"),
            "bank": entry.get("bank_label"),
            "parser": entry.get("parser"),
        },
        "company": {
            "name": cp.get("name"),
            "address": cp.get("address_lines") or [],
        },
        "inputs": {
            "aecb": entry.get("aecb_input"),
            "months_in_business": entry.get("months_in_business_input"),
        },
        "score": {
            "value": entry.get("score"),
            "verdict": entry.get("verdict"),
            "summary": entry.get("sub"),
        },
        "metrics_table": _metrics_table_rows(entry.get("metrics") or {}),
        "observations": entry.get("points") or [],
        "model": entry.get("model_used"),
    }


def format_tabular_record(entry: dict) -> str:
    """Human-readable tabular block for admin review."""
    cp = entry.get("company_profile") or {}
    metrics = entry.get("metrics") or {}
    lines = [
        "",
        "=" * 78,
        f"  LINKIT FUNDABILITY ANALYSIS  |  {entry.get('timestamp', '')}",
        "=" * 78,
        "",
        "COMPANY",
        "-" * 78,
        f"  {_pad('Name')}{cp.get('name', '—')}",
    ]
    if cp.get("address_lines"):
        for i, addr in enumerate(cp["address_lines"]):
            key = "Address" if i == 0 else ""
            lines.append(f"  {_pad(key)}{addr}")
    elif cp.get("full_block") and cp.get("name"):
        for extra in cp["full_block"].split("\n")[1:]:
            if extra.strip():
                lines.append(f"  {' ' * 28}{extra.strip()}")

    lines += [
        "",
        "SCORE SUMMARY",
        "-" * 78,
        f"  {_pad('Fundability Score')}{entry.get('score', '—')} / 100",
        f"  {_pad('Verdict')}{entry.get('verdict', '—')}",
        f"  {_pad('Summary')}{entry.get('sub', '—')}",
        f"  {_pad('Bank')}{entry.get('bank_label', '—')}",
        f"  {_pad('Parser')}{entry.get('parser', '—')}",
        f"  {_pad('Source file')}{entry.get('source_filename', '—')}",
        f"  {_pad('AECB (user input)')}{entry.get('aecb_input', '—')}",
        f"  {_pad('Months in business')}{entry.get('months_in_business_input', '—')}",
        f"  {_pad('Model')}{entry.get('model_used', '—')}",
        "",
        "KEY METRICS",
        "-" * 78,
        f"  {'Metric':<30} {'Value':>18}  {'Assessment':<24}",
        f"  {'-' * 30} {'-' * 18}  {'-' * 24}",
    ]

    metric_rows = [
        ("Avg monthly revenue", metrics.get("avg_monthly_revenue"), "AED"),
        ("Closing balance", metrics.get("closing_balance"), "AED"),
        ("Opening balance", metrics.get("opening_balance"), "AED"),
        ("Total credits (period)", metrics.get("total_credits_6m"), "AED"),
        ("Total debits (period)", metrics.get("total_debits_6m"), "AED"),
        ("Credit transactions", metrics.get("num_credit_txns"), ""),
        ("Debit transactions", metrics.get("num_debit_txns"), ""),
        ("Period (months)", metrics.get("period_months"), ""),
        ("Cash deposit mix", metrics.get("cash_deposit_pct"), "%"),
        ("Cheque returns", metrics.get("cheque_bounces"), ""),
        ("ATM withdrawals", metrics.get("atm_withdrawals"), ""),
        ("Unique payers", metrics.get("unique_inflow_sources"), ""),
    ]
    for label, val, suffix in metric_rows:
        if val is None:
            display = "—"
        elif suffix == "AED" and isinstance(val, (int, float)):
            display = f"AED {val:,}"
        elif suffix == "%" and isinstance(val, (int, float)):
            display = f"{val}%"
        else:
            display = str(val)
        lines.append(f"  {label:<30} {display:>18}  ")

    points = entry.get("points") or []
    if points:
        lines += ["", "AI OBSERVATIONS", "-" * 78]
        for i, p in enumerate(points, 1):
            lines.append(f"  {i}. {p}")

    lines.append("")
    return "\n".join(lines)


def _load_json_array() -> list:
    if not JSON_PATH.exists():
        return []
    try:
        return json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def append_admin_record(record: dict) -> None:
    """Append structured JSON + tabular log. Never raises to callers."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **record,
    }
    structured = structure_entry_for_json(entry)

    with JSONL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(structured, ensure_ascii=False) + "\n")

    entries = _load_json_array()
    entries.append(structured)
    JSON_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with TABLE_PATH.open("a", encoding="utf-8") as f:
        f.write(format_tabular_record(entry))
