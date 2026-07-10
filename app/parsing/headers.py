"""Per-bank statement header parsers."""
import re

from app.parsing.blocks import (
    extract_raw_blocks,
    parse_wio_currency_breakdown,
    sum_from_balance_blocks,
    wio_detail_page,
)
from app.parsing.company import extract_statement_period
from app.parsing.pdf import build_header, period_months_from_dates, pnum

# ── PER-BANK HEADER PARSERS ─────────────────────────────────────

def parse_header_adcb_detailed(page1: str) -> dict:
    total_credit  = pnum(page1, r"Total Credit Amount[:\s]+([\d,]+\.?\d*)")
    total_debit   = pnum(page1, r"Total Debit Amount[:\s]+([\d,]+\.?\d*)")
    closing_bal   = pnum(page1, r"Closing\(Available\) Balance[:\s]+([\d,]+\.?\d*)")
    opening_bal   = pnum(page1, r"Opening Balance[:\s]+([\d,]+\.?\d*)")
    num_credits   = int(pnum(page1, r"Total no of credits[:\s]+(\d+)"))
    num_debits    = int(pnum(page1, r"Total no of debits[:\s]+(\d+)"))
    avg_balance   = pnum(page1, r"Average Balance[:\s]+([\d,]+\.?\d*)")

    start_m = re.search(r"Start Date[:\s]+(\d{2}-\w+-\d{4})", page1, re.IGNORECASE)
    end_m   = re.search(r"End Date[:\s]+(\d{2}-\w+-\d{4})", page1, re.IGNORECASE)
    period_months = 6
    if start_m and end_m:
        period_months = period_months_from_dates(start_m.group(1), end_m.group(1))

    return build_header(
        total_credit, total_debit, closing_bal, opening_bal,
        period_months, num_credits, num_debits, avg_balance,
    )


def parse_header_adcb_legacy(full_text: str, page1: str, raw_blocks: list) -> dict:
    period_m = re.search(
        r"Statement Period\s+(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})",
        page1, re.IGNORECASE,
    )
    period_months = period_months_from_dates(period_m.group(1), period_m.group(2)) if period_m else 12

    bf_m = re.search(r"(\d{2}/\d{2}/\d{4})\s+B/F[^\d]*([\d,]+\.\d{2})", page1)
    opening_bal = float(bf_m.group(2).replace(",", "")) if bf_m else 0.0

    all_amounts = re.findall(r"([\d,]+\.\d{2})\s*$", full_text, re.MULTILINE)
    closing_bal = float(all_amounts[-1].replace(",", "")) if all_amounts else 0.0

    total_credit, total_debit, num_credits, num_debits = sum_from_balance_blocks(
        raw_blocks, opening_bal,
    )
    return build_header(
        total_credit, total_debit, closing_bal, opening_bal,
        period_months, num_credits, num_debits,
    )


def parse_header_rak(page1: str, raw_blocks: list) -> dict:
    opening_bal  = pnum(page1, r"Opening Balance\s+AED\s+([\d,]+\.?\d*)")
    total_credit = pnum(page1, r"Total Deposits\s+AED\s+([\d,]+\.?\d*)")
    total_debit  = pnum(page1, r"Total Withdrawals\s+AED\s+([\d,]+\.?\d*)")
    closing_bal  = pnum(page1, r"Closing Balance\s+AED\s+([\d,]+\.?\d*)")

    period_m = re.search(
        r"Statement Period:\s+(\d{2}-\w+-\d{4})\s+to\s+(\d{2}-\w+-\d{4})",
        page1, re.IGNORECASE,
    )
    period_months = period_months_from_dates(period_m.group(1), period_m.group(2)) if period_m else 1

    _, _, num_credits, num_debits = sum_from_balance_blocks(raw_blocks, opening_bal or None)

    return build_header(
        total_credit, total_debit, closing_bal, opening_bal, period_months,
        num_credits, num_debits,
    )


def parse_header_enbd(full_text: str, page1: str, raw_blocks: list) -> dict:
    period_info = extract_statement_period(full_text, [page1], "enbd")
    if period_info["start"] and period_info["end"]:
        period_months = period_months_from_dates(period_info["start"], period_info["end"])
    else:
        period_months = 1

    bf_m = re.search(r"BROUGHT FORWARD\s+([\d,]+\.\d{2})Cr", full_text, re.IGNORECASE)
    opening_bal = float(bf_m.group(1).replace(",", "")) if bf_m else 0.0

    cf_matches = re.findall(r"CARRIED FORWARD\s+([\d,]+\.\d{2})Cr", full_text, re.IGNORECASE)
    closing_bal = float(cf_matches[-1].replace(",", "")) if cf_matches else 0.0

    total_credit, total_debit, num_credits, num_debits = sum_from_balance_blocks(
        raw_blocks, opening_bal,
    )
    return build_header(
        total_credit, total_debit, closing_bal, opening_bal,
        period_months, num_credits, num_debits,
    )


def parse_header_wio(pages: list, full_text: str) -> dict:
    breakdown = parse_wio_currency_breakdown(pages)
    aed = breakdown.get("AED", {})

    period_m = re.search(
        r"FROM\s+(\d{2}/\d{2}/\d{4})\s+TO\s+(\d{2}/\d{2}/\d{4})",
        full_text, re.IGNORECASE,
    )
    period_months = period_months_from_dates(period_m.group(1), period_m.group(2)) if period_m else 6

    num_credits_total = sum(c["num_credit_txns"] for c in breakdown.values())
    num_debits_total  = sum(c["num_debit_txns"] for c in breakdown.values())

    result = build_header(
        aed.get("total_credits", 0),
        aed.get("total_debits", 0),
        aed.get("closing_balance", 0),
        aed.get("opening_balance", 0),
        period_months,
        num_credits_total,
        num_debits_total,
    )
    result["currency_breakdown"] = breakdown
    result["primary_currency"] = "AED"
    return result


def parse_header_generic(full_text: str, page1: str, raw_blocks: list) -> dict:
    """Heuristic header parser for banks without a dedicated layout."""
    credit_patterns = (
        r"Total Credit Amount[:\s]+([\d,]+\.?\d*)",
        r"Total Credits?[:\s]+AED\s*([\d,]+\.?\d*)",
        r"Total Credits?[:\s]+([\d,]+\.?\d*)",
        r"Total Deposits\s+AED\s*([\d,]+\.?\d*)",
        r"Total Deposits[:\s]+([\d,]+\.?\d*)",
    )
    debit_patterns = (
        r"Total Debit Amount[:\s]+([\d,]+\.?\d*)",
        r"Total Debits?[:\s]+AED\s*([\d,]+\.?\d*)",
        r"Total Debits?[:\s]+([\d,]+\.?\d*)",
        r"Total Withdrawals\s+AED\s*([\d,]+\.?\d*)",
        r"Total Withdrawals[:\s]+([\d,]+\.?\d*)",
    )
    opening_patterns = (
        r"Opening Balance[:\s]+AED\s*([\d,]+\.?\d*)",
        r"Opening Balance[:\s]+([\d,]+\.?\d*)",
        r"Opening\s*\(Available\)\s*Balance[:\s]+([\d,]+\.?\d*)",
    )
    closing_patterns = (
        r"Closing\(Available\)\s*Balance[:\s]+([\d,]+\.?\d*)",
        r"Closing Balance[:\s]+AED\s*([\d,]+\.?\d*)",
        r"Closing Balance[:\s]+([\d,]+\.?\d*)",
        r"Available Balance[:\s]+([\d,]+\.?\d*)",
    )

    def first_match(patterns: tuple[str, ...]) -> float:
        for pat in patterns:
            val = pnum(full_text, pat)
            if val:
                return val
        return 0.0

    total_credit = first_match(credit_patterns)
    total_debit = first_match(debit_patterns)
    opening_bal = first_match(opening_patterns)
    closing_bal = first_match(closing_patterns)
    num_credits = int(pnum(full_text, r"Total no of credits[:\s]+(\d+)")) or 0
    num_debits = int(pnum(full_text, r"Total no of debits[:\s]+(\d+)")) or 0
    avg_balance = pnum(full_text, r"Average Balance[:\s]+([\d,]+\.?\d*)")

    period_months = 6
    period_patterns = (
        r"Start Date[:\s]+(\d{2}-\w+-\d{4}).*?End Date[:\s]+(\d{2}-\w+-\d{4})",
        r"FROM\s+(\d{2}/\d{2}/\d{4})\s+TO\s+(\d{2}/\d{2}/\d{4})",
        r"Statement Period[:\s]+(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})",
        r"Statement Period:\s+(\d{2}-\w+-\d{4})\s+to\s+(\d{2}-\w+-\d{4})",
        r"Period[:\s]+(\d{2}/\d{2}/\d{4})\s*[-–]\s*(\d{2}/\d{2}/\d{4})",
    )
    for pat in period_patterns:
        m = re.search(pat, full_text, re.IGNORECASE | re.DOTALL)
        if m:
            period_months = period_months_from_dates(m.group(1), m.group(2))
            break

    if not (total_credit or total_debit) and raw_blocks:
        bf_m = re.search(r"(\d{2}/\d{2}/\d{4})\s+B/F[^\d]*([\d,]+\.\d{2})", page1)
        if bf_m and not opening_bal:
            opening_bal = float(bf_m.group(2).replace(",", ""))
        tc, td, nc, nd = sum_from_balance_blocks(raw_blocks, opening_bal or None)
        total_credit = total_credit or tc
        total_debit = total_debit or td
        num_credits = num_credits or nc
        num_debits = num_debits or nd

    if not closing_bal:
        amounts = re.findall(r"([\d,]+\.\d{2})\s*$", full_text, re.MULTILINE)
        if amounts:
            closing_bal = float(amounts[-1].replace(",", ""))

    return build_header(
        total_credit, total_debit, closing_bal, opening_bal,
        period_months, num_credits, num_debits, avg_balance,
    )


def parse_header(full_text: str, pages: list, bank: str, raw_blocks: list) -> dict:
    page1 = pages[0]
    parsers = {
        "adcb_detailed": lambda: parse_header_adcb_detailed(page1),
        "adcb_legacy":   lambda: parse_header_adcb_legacy(full_text, page1, raw_blocks),
        "enbd":          lambda: parse_header_enbd(full_text, page1, raw_blocks),
        "rak":           lambda: parse_header_rak(page1, raw_blocks),
        "wio":           lambda: parse_header_wio(pages, full_text),
        "generic":       lambda: parse_header_generic(full_text, page1, raw_blocks),
    }
    return parsers.get(bank, lambda: {})()


def extract_transaction_blocks(pages: list, bank: str) -> list:
    """Return description blocks for signal detection (excludes carry-forward rows)."""
    raw = extract_raw_blocks(pages, bank)
    skip_re = re.compile(r"BROUGHT FORWARD|CARRIED FORWARD|\bB/F\b", re.IGNORECASE)

    if bank == "adcb_detailed":
        return [b[:250] for b in raw]

    start_re = {
        "adcb_legacy": r"\d{2}/\d{2}/\d{4}",
        "enbd":        r"\d{2}[A-Z]{3}\d{2}",
        "rak":         r"\d{2}-[A-Z]{3}-\d{4}",
        "wio":         r"\d{2}/\d{2}/\d{4}\s+P\d+",
        "generic":     r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}-[A-Z]{3}-\d{4}|\d{2}[A-Z]{3}\d{2}",
    }.get(bank, r".")
    return [
        b[:250] for b in raw
        if re.match(start_re, b.strip())
        and not skip_re.search(b[:60])
    ]
