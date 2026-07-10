"""PDF text extraction and date helpers."""
import re
from datetime import datetime

from dateutil.relativedelta import relativedelta
from pypdf import PdfReader

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
    "generic":       r"(?=\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s|\d{1,2}-[A-Z]{3}-\d{4}\s|\d{2}[A-Z]{3}\d{2}\s)",
}

GENERIC_LINE_DATE_RE = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}-[A-Z]{3}-\d{4}|\d{2}[A-Z]{3}\d{2})\s",
    re.IGNORECASE,
)

BANK_NAME_PATTERNS = (
    (r"Emirates NBD", "Emirates NBD"),
    (r"First Abu Dhabi|FAB", "FAB"),
    (r"Dubai Islamic|DIB", "Dubai Islamic Bank"),
    (r"Abu Dhabi Commercial|ADCB", "ADCB"),
    (r"RAKBANK|Ras Al Khaimah", "RAKBank"),
    (r"Wio Bank", "Wio Bank"),
    (r"Mashreq", "Mashreq"),
    (r"Commercial Bank of Dubai|CBD", "CBD"),
    (r"HSBC", "HSBC"),
    (r"Standard Chartered", "Standard Chartered"),
    (r"National Bank of Fujairah|NBF", "NBF"),
    (r"United Arab Bank|UAB", "UAB"),
    (r"Bank of Sharjah", "Bank of Sharjah"),
    (r"Sharjah Islamic", "Sharjah Islamic Bank"),
    (r"Abu Dhabi Islamic|ADIB", "ADIB"),
    (r"Al Hilal Bank", "Al Hilal Bank"),
    (r"National Bank of Umm Al Qaiwain|NBQ", "NBQ"),
    (r"Citibank", "Citibank"),
    (r"Emirates Islamic", "Emirates Islamic Bank"),
)


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
