import os
import re
import json
from datetime import datetime
from dateutil.relativedelta import relativedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
from pypdf import PdfReader
from dotenv import load_dotenv
from llm import call_openrouter

load_dotenv()

app = Flask(__name__)
CORS(app)


# ── HELPERS ─────────────────────────────────────────────────────

def pnum(text: str, pattern: str) -> float:
    """Extract a single number from text using regex."""
    m = re.search(pattern, text, re.IGNORECASE)
    return float(m.group(1).replace(",", "")) if m else 0.0


def extract_pages(file) -> list:
    """Return list of raw text strings, one per page."""
    reader = PdfReader(file)
    return [(page.extract_text() or "") for page in reader.pages]


DATE_FORMATS = (
    "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d-%m-%Y",
    "%d-%b-%y", "%d/%m/%y", "%d-%m-%y",
)

BLOCK_SPLITTERS = {
    "adcb_detailed": r"\n\s*\d+\s+\d{2}-\w{3}-\n?\d{4}",
    "adcb_legacy":   r"(?=\d{2}/\d{2}/\d{4}\s)",
    "enbd":          r"(?=\d{2}[A-Z]{3}\d{2}\s)",
    "rak":           r"(?=\d{2}-[A-Z]{3}-\d{4}\s)",
    "wio":           r"(?=\d{2}/\d{2}/\d{4}\s+P\d+)",
}


def parse_date(text: str):
    """Try common UAE bank statement date formats."""
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    m = re.match(r"(\d{2})([A-Z]{3})(\d{2})", text, re.IGNORECASE)
    if m:
        try:
            return datetime.strptime(m.group(0).upper(), "%d%b%y")
        except ValueError:
            pass
    return None


def period_months_from_dates(start_text: str, end_text: str, default: int = 6) -> int:
    start_dt = parse_date(start_text)
    end_dt = parse_date(end_text)
    if start_dt and end_dt:
        diff = relativedelta(end_dt, start_dt)
        return max(1, diff.months + diff.years * 12 + (1 if diff.days > 0 else 0))
    return default


def build_header(
    total_credit: float,
    total_debit: float,
    closing_bal: float,
    opening_bal: float,
    period_months: int,
    num_credits: int = 0,
    num_debits: int = 0,
    avg_balance: float = 0,
) -> dict:
    if not avg_balance and opening_bal and closing_bal:
        avg_balance = (opening_bal + closing_bal) / 2
    avg_monthly_revenue = round(total_credit / period_months) if total_credit else 0
    return {
        "total_credits_6m":     round(total_credit),
        "total_debits_6m":      round(total_debit),
        "closing_balance":      round(closing_bal),
        "opening_balance":      round(opening_bal),
        "avg_balance":          round(avg_balance),
        "num_credit_txns":      num_credits,
        "num_debit_txns":       num_debits,
        "period_months":        period_months,
        "avg_monthly_revenue":  avg_monthly_revenue,
    }


def detect_bank(full_text: str, page1: str) -> str:
    """Identify bank from PDF text. Order matters — most specific first."""
    if re.search(r"Total Credit Amount", full_text, re.IGNORECASE):
        return "adcb_detailed"
    if re.search(r"Wio Bank|wio bank", full_text, re.IGNORECASE):
        return "wio"
    if re.search(r"RAKBANK|Ras Al Khaimah", full_text, re.IGNORECASE):
        return "rak"
    if re.search(r"Emirates NBD", full_text, re.IGNORECASE):
        return "enbd"
    if re.search(r"Mashreq|Mashreqbank", full_text, re.IGNORECASE):
        return "mashreq"
    if re.search(
        r"Statement Period|CDM-Cash Deposit|VIRTUAL BRANCH|600 50 2030",
        page1,
        re.IGNORECASE,
    ):
        return "adcb_legacy"
    return "unknown"


def extract_blocks_line_joined(full_text: str) -> list:
    """Join multiline transactions for banks where rows span multiple lines."""
    blocks = []
    current = []
    for line in full_text.split("\n"):
        stripped = line.strip()
        if re.match(r"^\d{2}/\d{2}/\d{4}\s", stripped):
            if current:
                blocks.append("\n".join(current))
            current = [stripped]
        elif current:
            current.append(stripped)
    if current:
        blocks.append("\n".join(current))
    return [b for b in blocks if re.findall(r"[\d,]+\.\d{2}", b)]


def extract_raw_blocks(pages: list, bank: str) -> list:
    """Split statement into transaction blocks before filtering carry-forward rows."""
    if bank == "wio":
        full_text = wio_all_sections_text(pages)
    else:
        full_text = "\n".join(pages)

    if bank == "adcb_detailed":
        blocks = re.split(BLOCK_SPLITTERS["adcb_detailed"], full_text)
        return blocks[1:]

    if bank == "adcb_legacy":
        return extract_blocks_line_joined(full_text)

    splitter = BLOCK_SPLITTERS.get(bank)
    if not splitter:
        return []

    blocks = re.split(splitter, full_text)
    start_re = {
        "enbd": r"\d{2}[A-Z]{3}\d{2}",
        "rak":  r"\d{2}-[A-Z]{3}-\d{4}",
        "wio":  r"\d{2}/\d{2}/\d{4}\s+P\d+",
    }.get(bank, r".")
    return [b for b in blocks if re.match(start_re, b.strip())]


def sum_from_balance_blocks(blocks: list, opening_bal: float | None = None) -> tuple:
    """
    Sum credits/debits by tracking running balance changes.
    Handles RAK/ENBD 'amount balanceCr', and ADCB/ENBD brought-forward layouts.
    """
    total_credit = total_debit = 0.0
    num_credits = num_debits = 0
    prev_bal = opening_bal

    for block in blocks:
        txn_part = re.split(
            r"The National Bank of Ras Al Khaimah|Emirates NBD Bank \(P\.J\.S\.C\.\)|Page \[",
            block,
        )[0]

        if re.search(r"BROUGHT FORWARD|\bB/F\b", txn_part[:80], re.IGNORECASE):
            bf_m = re.search(r"([\d,]+\.\d{2})(?:Cr)?", txn_part)
            if bf_m:
                prev_bal = float(bf_m.group(1).replace(",", ""))
            continue
        if re.search(r"CARRIED FORWARD", txn_part[:80], re.IGNORECASE):
            continue

        rak_m = re.search(r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})Cr", txn_part)
        if rak_m and prev_bal is not None:
            txn_amt = float(rak_m.group(1).replace(",", ""))
            new_bal = float(rak_m.group(2).replace(",", ""))
            if new_bal > prev_bal + 0.001:
                total_credit += txn_amt
                num_credits += 1
            elif new_bal < prev_bal - 0.001:
                total_debit += txn_amt
                num_debits += 1
            prev_bal = new_bal
            continue

        cr_line = re.search(r"([\d,]+\.\d{2})\s*Cr\s*$", txn_part, re.M)
        amounts = re.findall(r"([\d,]+\.\d{2})", txn_part)

        if cr_line:
            new_bal = float(cr_line.group(1).replace(",", ""))
            line_before_cr = txn_part[:cr_line.start()]
            line_amounts = re.findall(r"([\d,]+\.\d{2})", line_before_cr)
            txn_amounts = line_amounts if line_amounts else amounts[:-1]
        elif len(amounts) >= 2:
            new_bal = float(amounts[-1].replace(",", ""))
            txn_amounts = amounts[:-1]
        else:
            continue

        if not txn_amounts or prev_bal is None:
            if cr_line or len(amounts) >= 1:
                prev_bal = new_bal if cr_line or len(amounts) >= 2 else prev_bal
            continue

        txn_amt = float(txn_amounts[-1].replace(",", ""))
        if new_bal > prev_bal + 0.001:
            total_credit += txn_amt
            num_credits += 1
        elif new_bal < prev_bal - 0.001:
            total_debit += txn_amt
            num_debits += 1
        prev_bal = new_bal

    return total_credit, total_debit, num_credits, num_debits


def extract_wio_currency_sections(pages: list) -> dict[str, str]:
    """Split Wio statement pages into per-currency text sections."""
    sections: dict[str, list[str]] = {}
    current: str | None = None

    for page in pages:
        cur_m = re.search(r"CURRENCY\s*\n\s*(AED|USD|EUR|GBP)", page, re.IGNORECASE)
        paren_m = re.search(r"\(\s*\)\s*(AED|USD|EUR|GBP)", page)
        detected = (cur_m.group(1) if cur_m else paren_m.group(1) if paren_m else None)
        if detected:
            current = detected.upper()
            sections.setdefault(current, [])
        if current:
            sections.setdefault(current, []).append(page)

    return {currency: "\n".join(chunk) for currency, chunk in sections.items()}


def parse_wio_currency_breakdown(pages: list) -> dict:
    """Per-currency ledger stats from Wio multi-account statements."""
    sections = extract_wio_currency_sections(pages)
    breakdown = {}

    for currency in sorted(sections.keys()):
        text = sections[currency]
        total_credit, total_debit, num_credits, num_debits = sum_wio_transactions(text)
        detail_page = wio_detail_page(pages, currency)
        opening_bal = pnum(detail_page, r"OPENING BALANCE\s+([\d,]+\.?\d*)")
        closing_bal = pnum(detail_page, r"CLOSING BALANCE\s+([\d,]+\.?\d*)")

        if not (num_credits or num_debits or opening_bal or closing_bal):
            continue

        breakdown[currency] = {
            "num_credit_txns": num_credits,
            "num_debit_txns":  num_debits,
            "total_credits":   round(total_credit),
            "total_debits":    round(total_debit),
            "opening_balance": round(opening_bal),
            "closing_balance": round(closing_bal),
        }

    return breakdown


def wio_all_sections_text(pages: list) -> str:
    """Concatenate all Wio currency sections for cross-account transaction scanning."""
    sections = extract_wio_currency_sections(pages)
    return "\n".join(sections[c] for c in sorted(sections.keys()))


def wio_primary_section(pages: list, currency: str = "AED") -> str:
    """Return transaction text for one Wio currency section."""
    sections = extract_wio_currency_sections(pages)
    return sections.get(currency.upper(), "\n".join(pages))


def wio_detail_page(pages: list, currency: str = "AED") -> str:
    """Find the account summary page for a Wio currency section."""
    pattern = re.compile(rf"CURRENCY\s*\n\s*{currency}", re.IGNORECASE)
    for page in pages:
        if pattern.search(page) and "OPENING BALANCE" in page:
            return page
    return next((p for p in pages if "OPENING BALANCE" in p), pages[0])


def sum_wio_transactions(full_text: str) -> tuple:
    """Sum Wio credits/debits from signed Amount column."""
    total_credit = total_debit = 0.0
    num_credits = num_debits = 0
    for m in re.finditer(
        r"\d{2}/\d{2}/\d{4}\s+P\d+\s+(.+?)\s+(-?[\d,]+\.?\d*)\s+[\d,]+\.?\d*\s*$",
        full_text,
        re.MULTILINE,
    ):
        amt = float(m.group(2).replace(",", ""))
        if amt > 0:
            total_credit += amt
            num_credits += 1
        elif amt < 0:
            total_debit += abs(amt)
            num_debits += 1
    return total_credit, total_debit, num_credits, num_debits


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
    period_m = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})", page1)
    period_months = period_months_from_dates(period_m.group(1), period_m.group(2)) if period_m else 1

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


def parse_header(full_text: str, pages: list, bank: str, raw_blocks: list) -> dict:
    page1 = pages[0]
    parsers = {
        "adcb_detailed": lambda: parse_header_adcb_detailed(page1),
        "adcb_legacy":   lambda: parse_header_adcb_legacy(full_text, page1, raw_blocks),
        "enbd":          lambda: parse_header_enbd(full_text, page1, raw_blocks),
        "rak":           lambda: parse_header_rak(page1, raw_blocks),
        "wio":           lambda: parse_header_wio(pages, full_text),
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
    }.get(bank, r".")
    return [
        b[:250] for b in raw
        if re.match(start_re, b.strip())
        and not skip_re.search(b[:60])
    ]


BANK_LABELS = {
    "adcb_detailed": "ADCB",
    "adcb_legacy":   "ADCB",
    "enbd":          "Emirates NBD",
    "rak":           "RAKBank",
    "wio":           "Wio Bank",
    "mashreq":       "Mashreq",
    "unknown":       "Unknown",
}


# ── TRANSACTION PARSER (all banks) ──────────────────────────────

BOUNCE_RE = re.compile(
    r"CHQ\s*RETRN|CHQ\s*RETURN(?!\s*CHARGE)|CHEQUE\s*RETURN(?!\s*CHARGE)|RETURNED\s*CHEQUE"
    r"|CHEQUE\s*REJECT|CHQ\s*REJECT|INSUFFICIENT\s*FUNDS"
    r"|UNPAID\s*CHEQUE|CHEQUE\s*BOUNCE",
    re.IGNORECASE,
)
BOUNCE_EXCLUDE_RE = re.compile(
    r"CHEQUE\s*DEPOSIT|CHEQUE\s*BOOK|CLEARING\s*CHEQUE|IN-HOUSE\s*CHEQUE"
    r"|CLG\s*CHQ\.DRAWN|CHEQUE\s*DEPOSIT\s*AT|CHEQUE\s*RETURN\s*CHARGE|CHQ\s*RETURN\s*CHARGE",
    re.IGNORECASE,
)
CDM_RE = re.compile(
    r"CDM-Cash\s*Deposit|CDM\s*CASH|CASH\s*DEPOSIT\s*(?:AT|VIA|-)"
    r"|CHEQUE\s*DEPOSIT\s*AT\s+\w+\s*CDM"
    r"|SDM\s*DEPOSIT|SMART\s*DEPOSIT|BULK\s*CASH\s*MACHINE"
    r"|ATM\s+CASH\s+DEPOSIT",
    re.IGNORECASE,
)
ATM_RE = re.compile(
    r"ATM\s*WDL|ATM\s*WTH|ATM\s*WITHDRAWAL|ATM\s+CASH\s+WITHDRAWAL",
    re.IGNORECASE,
)
CASH_WITHDRAWAL_RE = re.compile(r"CASH\s*WITHDRAWAL\s+AT", re.IGNORECASE)
ATM_FEE_RE = re.compile(r"ATM.*(CHG|FEE)|WDL\s*CHG|ENCASHMENT\s*CHARGE", re.IGNORECASE)
POS_RE = re.compile(r"\bPUR\b|PURCHASE\s*TRXN", re.IGNORECASE)
CHEQUE_DEPOSIT_RE = re.compile(
    r"CHEQUE\s*DEPOSIT|INHOUSE\s*CHEQUE\s*DEPOSIT|IN-HOUSE\s*CHEQUE\s*TRANSFER"
    r"|CHEQUE\s*DEPOSIT\s*AT",
    re.IGNORECASE,
)
NI_POS_RE = re.compile(r"NI\s*POS|NETWORK\s*IN(?:TERNATIONAL)?", re.IGNORECASE)
REMITTER_RES = (
    re.compile(r"B/O[_\s]+([A-Z][A-Z0-9\s\-&\.]{3,39}?)[\n\r]", re.IGNORECASE),
    re.compile(
        r"From\s+([A-Z][A-Z0-9\s\-&\.\,\']{3,50}?)(?:\s+P\s*O\s*Box|\s+\d|\s+\(|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"INWARD\s+REMITTANCE[\s\S]{0,160}?(?:/\d+\s+)?([A-Z][A-Z0-9\s\-&\.]{4,45}?)(?:\s+/REF/|\s+LL\b|\s+LLC\b|\s+CO\b)",
        re.IGNORECASE,
    ),
    re.compile(r"INWARD\s+(?:REMITTANCE|T/T)[^\n]{0,80}?([A-Z][A-Z0-9\s\-&\.]{4,40})", re.IGNORECASE),
    re.compile(r"INWARD\s+T/T\s+([A-Z][A-Z0-9\s\-&\.]{4,45})", re.IGNORECASE),
    re.compile(r"MBTRF\s+B/O\s+([A-Z][A-Z0-9\s\-&\.]{3,40})", re.IGNORECASE),
)


def is_bounce(block: str) -> bool:
    # ADCB detailed repeats description in Payment Details — count description column only
    head = block[:200]
    return bool(BOUNCE_RE.search(head)) and not BOUNCE_EXCLUDE_RE.search(head)


def extract_credit_amount(block: str) -> float | None:
    """
    Pull the credit (deposit) amount from a transaction block.
    ADCB detailed: ' - <credit> <balance>'
    ENBD / columnar: '<credit> <balance>Cr'
    """
    m = re.search(r"-\s+([\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})(?:\s|$)", block, re.MULTILINE)
    if m:
        return float(m.group(1).replace(",", ""))

    m = re.search(r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})Cr", block)
    if m:
        return float(m.group(1).replace(",", ""))

    m = re.search(
        r"From\s+.+?\s+([\d,]+\.?\d*)\s+[\d,]+\.?\d*\s*$",
        block,
        re.MULTILINE | re.IGNORECASE,
    )
    if m:
        val = float(m.group(1).replace(",", ""))
        return val if val > 0 else None

    # Wio single-line: date Pxxx description <amount> <balance>
    m = re.search(
        r"\d{2}/\d{2}/\d{4}\s+P\d+\s+.+?\s+(-?[\d,]+\.?\d*)\s+[\d,]+\.?\d*\s*$",
        block.strip(),
        re.MULTILINE,
    )
    if m:
        val = float(m.group(1).replace(",", ""))
        return val if val > 0 else None

    amounts = re.findall(r"([\d,]+\.\d{2})", block)
    if len(amounts) >= 2:
        val = float(amounts[-2].replace(",", ""))
        if 0 < val < 5000000:
            return val
    return None


def normalize_remitter(name: str) -> str:
    name = re.sub(r"\s+", " ", name.strip())
    name = re.sub(r"\s+-\s+\d+.*$", "", name)
    name = re.sub(r"\s+-\s*$", "", name)
    return name.upper()


REMITTER_SKIP_RE = re.compile(
    r"B/O_NETWORK|NI\s*POS|NETWORK\s*INTERNATIONAL|TRF\s+TO|O/W\s+TRF"
    r"|FUNDS\s+TRANSFER\s+WITHIN|TRANSFER\s+WITHIN",
    re.IGNORECASE,
)


def extract_remitter_from_block(block: str, bank: str) -> str | None:
    if REMITTER_SKIP_RE.search(block):
        return None

    if bank == "adcb_detailed":
        m = re.search(r"MBTRF\s+B/O\s+([A-Z][A-Z0-9\s\-&\.]{5,40}?)(?:\s+\d|\s+ADC|\s+EBIL|\n)", block, re.IGNORECASE)
        if m:
            return normalize_remitter(m.group(1))

        m = re.search(r"B/O_([A-Z0-9]+(?:_[A-Z0-9]+)*)_EBIL", block, re.IGNORECASE)
        if m:
            return normalize_remitter(" ".join(m.group(1).split("_")))

        m = re.search(r"B/O_([A-Z][A-Z0-9\s\-&\.]{4,40}?)[_\s]+EBIL", block, re.IGNORECASE)
        if m:
            return normalize_remitter(m.group(1))
        return None

    if bank == "enbd":
        if re.search(r"SDM\s*DEPOSIT", block, re.IGNORECASE):
            return "SDM CASH DEPOSIT"

        m = re.search(
            r"MOBILE\s+BANKING\s+TRANSFER\s+FROM\s+([A-Z][A-Z\s]+?)(?:\s+AE|\n)",
            block,
            re.IGNORECASE,
        )
        if m:
            return normalize_remitter(m.group(1))

        m = re.search(
            r"/005/002\s+([\s\S]+?)(?:\s+/REF/|\s+[\d,]+\.\d{2})",
            block,
            re.IGNORECASE,
        )
        if m:
            return normalize_remitter(m.group(1))

        m = re.search(
            r"INWARD\s+REMITTANCE[\s\S]{0,500}?1/SHARJAH\s+ISLAMIC\s+BANK",
            block,
            re.IGNORECASE,
        )
        if m:
            return "SHARJAH ISLAMIC BANK"

        m = re.search(
            r"INWARD\s+REMITTANCE[\s\S]{0,500}?AED\s+[\d,.]+\s+([\s\S]+?)\s+POBOX",
            block,
            re.IGNORECASE,
        )
        if m:
            return normalize_remitter(m.group(1))

        return None

    if bank == "rak":
        if re.search(r"ATM\s+CASH\s+DEPOSIT", block, re.IGNORECASE):
            return "ATM CASH DEPOSIT"

        m = re.search(
            r"INWARD\s+T/T\s+([A-Z][A-Z0-9\s\-&\.]+?)(?:\s+/REF/|\s+[\d,]+\.\d{2})",
            block,
            re.IGNORECASE,
        )
        if m:
            return normalize_remitter(m.group(1))

        m = re.search(r"CHEQUE\s+DEPOSIT\s+\d+\s+([A-Z]+)\s+DXB", block, re.IGNORECASE)
        if m:
            return normalize_remitter(f"CHEQUE VIA {m.group(1)}")

        return None

    for pattern in REMITTER_RES:
        m = pattern.search(block)
        if m:
            name = normalize_remitter(m.group(1))
            if len(name) > 3 and name not in {"NETWORK", "LLC", "CO", "LTD"}:
                return name
    return None


def count_unique_remitters(blocks: list, bank: str, raw_blocks: list | None) -> int:
    """Count distinct external payers; columnar banks need full multiline blocks."""
    use_raw = bank in ("adcb_detailed", "enbd", "rak", "wio") and raw_blocks
    source_blocks = raw_blocks if use_raw else blocks
    remitter_set = set()
    for block in source_blocks:
        name = extract_remitter_from_block(block, bank)
        if name:
            remitter_set.add(name)
    return len(remitter_set)


def parse_transactions(
    blocks: list,
    total_credit: float,
    bank: str = "unknown",
    raw_blocks: list | None = None,
) -> dict:
    """Count transaction-level signals from per-block descriptions."""

    bounces = sum(1 for b in blocks if is_bounce(b))
    inward_bounces = sum(
        1 for b in blocks
        if re.search(
            r"CHQ\s*RETRN|I/?W\s*CHQ\s*RETURN(?!\s*CHARGE)|INWARD.*RETURN",
            b[:200], re.IGNORECASE,
        )
        and not BOUNCE_EXCLUDE_RE.search(b[:200])
    )

    cdm_blocks = [b for b in (raw_blocks or blocks) if CDM_RE.search(b)]
    cdm_count  = len(cdm_blocks)

    cdm_total = 0.0
    for b in cdm_blocks:
        amt = extract_credit_amount(b)
        if amt and 0 < amt < 5000000:
            cdm_total += amt

    cash_deposit_pct = round(cdm_total / total_credit * 100) if total_credit else 0

    atm_count = sum(
        1 for b in blocks
        if (ATM_RE.search(b) or CASH_WITHDRAWAL_RE.search(b)) and not ATM_FEE_RE.search(b)
    )

    pur_count = sum(1 for b in blocks if POS_RE.search(b))

    personal_brands = re.compile(
        r"\bAPPLE\b|\bAMAZON\b|\bNOON\b|\bLINKEDIN\b|\bMAKEMYTRIP\b"
        r"|\bPARKONIC\b|\bCARREFOUR\b|\bTALABAT\b|\bNETFLIX\b|\bSPOTIFY\b"
        r"|\bIKEA\b|\bSOUVENIR\b|\bQATAR AIR\b|\bGIORDANO\b|\bCOCONUT\b"
        r"|\bHAYYA\b|\bSANGEETHA\b|\bNASEAM\b",
        re.IGNORECASE,
    )
    brand_count = sum(1 for b in blocks if personal_brands.search(b))

    adnoc_count = sum(1 for b in blocks if re.search(r"\bADNOC\b|\bENOC\b", b, re.IGNORECASE))

    personal_spend_flags = pur_count + brand_count + (adnoc_count // 2)

    unique_inflow_sources = count_unique_remitters(blocks, bank, raw_blocks)

    ni_pos_count = sum(1 for b in blocks if NI_POS_RE.search(b))

    cheque_deposits_in = sum(
        1 for b in blocks
        if CHEQUE_DEPOSIT_RE.search(b) and not is_bounce(b)
    )

    return {
        "cheque_bounces":        bounces,
        "inward_bounces":        inward_bounces,
        "cdm_count":             cdm_count,
        "cdm_total_aed":         round(cdm_total),
        "cash_deposit_pct":      cash_deposit_pct,
        "atm_withdrawals":       atm_count,
        "pur_transactions":      pur_count,
        "personal_spend_flags":  personal_spend_flags,
        "unique_inflow_sources": unique_inflow_sources,
        "ni_pos_settlements":    ni_pos_count,
        "cheque_deposits_in":    cheque_deposits_in,
        "zero_revenue_months":   0,
    }


# ── SCORE (rule-based, no LLM) ───────────────────────────────────

def compute_score(m: dict) -> int:
    score = 50

    # AECB
    if m["aecb"] >= 660:        score += 15
    elif m["aecb"] >= 600:      score += 5
    else:                       score -= 15

    # Time in business
    if m["months_in_business"] >= 36:   score += 10
    elif m["months_in_business"] >= 6:  score += 3
    else:                               score -= 10

    # Monthly revenue
    rev = m["avg_monthly_revenue"]
    if rev >= 500000:    score += 15
    elif rev >= 100000:  score += 7
    elif rev >= 30000:   score += 0
    else:                score -= 10

    # Closing balance
    cb = m["closing_balance"]
    if cb >= 50000:    score += 5
    elif cb >= 10000:  score += 2
    else:              score -= 5

    # Cheque bounces (hard penalty)
    score -= min(m["cheque_bounces"] * 8, 24)

    # Cash deposit concentration
    if m["cash_deposit_pct"] > 40:    score -= 10
    elif m["cash_deposit_pct"] > 20:  score -= 4

    # ATM withdrawals from business account
    if m["atm_withdrawals"] > 15:   score -= 6
    elif m["atm_withdrawals"] > 8:  score -= 3

    # Personal spend
    if m["personal_spend_flags"] > 80:   score -= 8
    elif m["personal_spend_flags"] > 40: score -= 4
    elif m["personal_spend_flags"] > 15: score -= 2

    # Inflow diversity
    if m["unique_inflow_sources"] >= 10:  score += 8
    elif m["unique_inflow_sources"] >= 5: score += 4
    elif m["unique_inflow_sources"] >= 3: score += 2
    else:                                 score -= 5

    return max(5, min(95, score))


# ── LLM CALL (aggregated numbers only, one call) ─────────────────

SYSTEM_PROMPT = """You are a UAE SME lending analyst. You receive aggregated metrics extracted 
from a business bank statement. Produce a JSON response explaining the fundability score.

UAE lender thresholds for reference:
- Monthly revenue: >500k AED strong, >100k borderline, <100k weak
- AECB: >660 strong, 600-660 borderline, <600 likely rejection
- Business age: >36 months strong, 6-36 borderline, <6 weak
- Closing balance: >50k AED preferred
- Cheque bounces: zero preferred, any = risk signal
- Cash deposits (CDM): <20% of total inflows preferred
- ATM withdrawals on business account: flag if excessive
- Inflow diversity: 3+ unique sources preferred

Rules:
- Be factual and diplomatic. Do not accuse, just observe.
- Only use numbers that appear verbatim in the metrics JSON. Never invent sub-categories.
- cheque_bounces is the ONLY bounce figure. Never say "inward bounce" or "outward bounce".
- Never mention personal_spend_flags, pur_transactions, or inward_bounces in your points. Those are private.
- Your 4 points must only reference: avg_monthly_revenue, aecb, closing_balance, months_in_business, unique_inflow_sources, cash_deposit_pct, atm_withdrawals, cheque_bounces.

Return ONLY valid JSON, no markdown, no explanation:
{
  "verdict": "Strong" | "Moderate" | "Needs Work",
  "sub": "one sentence summary, max 12 words",
  "points": ["exactly 4 observations, each max 20 words, factual, diplomatic"]
}"""


def _rule_fallback(metrics: dict, verdict: str) -> dict:
    return {
        "verdict": verdict,
        "sub": "Analysis complete. Review metrics below.",
        "points": [
            f"Monthly revenue of {metrics.get('avg_monthly_revenue', 0):,} AED extracted from statement.",
            f"AECB score of {metrics.get('aecb', 0)} noted.",
            f"Closing balance of {metrics.get('closing_balance', 0):,} AED recorded.",
            f"Business operating for {metrics.get('months_in_business', 0)} months.",
        ],
    }


def call_analyser(metrics: dict, score: int) -> tuple[dict, str]:
    verdict = "Strong" if score >= 70 else "Moderate" if score >= 45 else "Needs Work"

    public = {
        "avg_monthly_revenue":  metrics.get("avg_monthly_revenue"),
        "aecb":                 metrics.get("aecb"),
        "months_in_business":   metrics.get("months_in_business"),
        "closing_balance":      metrics.get("closing_balance"),
        "cheque_bounces":       metrics.get("cheque_bounces"),
        "cash_deposit_pct":     metrics.get("cash_deposit_pct"),
        "atm_withdrawals":      metrics.get("atm_withdrawals"),
        "unique_inflow_sources":metrics.get("unique_inflow_sources"),
        "num_credit_txns":      metrics.get("num_credit_txns"),
        "period_months":        metrics.get("period_months"),
    }
    if metrics.get("currency_breakdown"):
        public["currency_breakdown"] = metrics["currency_breakdown"]
        public["primary_currency"] = metrics.get("primary_currency", "AED")

    payload = f"Score: {score}/100\nVerdict: {verdict}\nMetrics:\n{json.dumps(public, indent=2)}"

    result = call_openrouter(
        system=SYSTEM_PROMPT,
        user_content=payload,
        role="analyser",
        max_tokens=600,
    )
    model_used = result.get("model_used", "rule_fallback")

    if result.get("error") or not result.get("text"):
        return _rule_fallback(metrics, verdict), model_used

    raw = re.sub(r"```json|```", "", result["text"]).strip()
    try:
        return json.loads(raw), model_used
    except json.JSONDecodeError:
        return _rule_fallback(metrics, verdict), model_used


# ── ENDPOINT ─────────────────────────────────────────────────────

@app.route("/analyse", methods=["POST"])
def analyse():
    try:
        # Validate inputs
        aecb_raw   = request.form.get("aecb", "").strip()
        months_raw = request.form.get("months", "").strip()
        pdf        = request.files.get("pdf")

        if not aecb_raw or not months_raw:
            return jsonify({"error": "Missing AECB score or months in business."}), 400
        if not pdf:
            return jsonify({"error": "No PDF file received. Please upload your bank statement."}), 400
        if not pdf.filename.lower().endswith(".pdf"):
            return jsonify({"error": f"File '{pdf.filename}' is not a PDF. Only PDF files are accepted."}), 400

        try:
            aecb   = int(aecb_raw)
            months = int(months_raw)
        except ValueError:
            return jsonify({"error": "AECB score and months must be whole numbers."}), 400

        if not (300 <= aecb <= 900):
            return jsonify({"error": f"AECB score {aecb} is out of range. Must be between 300 and 900."}), 400
        if months < 1:
            return jsonify({"error": "Months in business must be at least 1."}), 400

        # Extract pages
        try:
            pages = extract_pages(pdf)
        except Exception as e:
            return jsonify({"error": f"Could not read PDF: {str(e)}. Make sure the file is not password protected."}), 400

        if len(pages) < 1:
            return jsonify({"error": "PDF appears to be empty or unreadable."}), 400

        full_text = "\n".join(pages)
        bank = detect_bank(full_text, pages[0])

        if bank == "unknown":
            return jsonify({
                "error": "Could not identify the bank from this PDF. "
                         "Supported banks: ADCB, Emirates NBD, RAKBank, Wio Bank, Mashreq."
            }), 400

        raw_blocks = extract_raw_blocks(pages, bank)
        blocks = extract_transaction_blocks(pages, bank)
        header = parse_header(full_text, pages, bank, raw_blocks)

        if header.get("total_credits_6m", 0) == 0 and header.get("closing_balance", 0) == 0:
            return jsonify({
                "error": f"Could not extract financial data from this {BANK_LABELS[bank]} statement. "
                         "Make sure the PDF is a complete, unprotected bank statement."
            }), 400

        transactions = parse_transactions(
            blocks, header["total_credits_6m"], bank=bank, raw_blocks=raw_blocks,
        )

        metrics = {
            "bank":             BANK_LABELS[bank],
            "aecb":             aecb,
            "months_in_business": months,
            **header,
            **transactions,
        }

        # Score
        score = compute_score(metrics)

        result, model_used = call_analyser(metrics, score)

        return jsonify({
            "score":      score,
            "metrics":    metrics,
            "verdict":    result.get("verdict", "Moderate"),
            "sub":        result.get("sub", ""),
            "points":     result.get("points", []),
            "model_used": model_used,
        })

    except Exception as e:
        return jsonify({
            "error": f"Unexpected server error: {str(e)}. Please try again or contact support."
        }), 500


if __name__ == "__main__":
    app.run(debug=True, port=5001)