"""Transaction block splitting and Wio section helpers."""
import re

from app.parsing.banks import BLOCK_SPLITTERS, GENERIC_LINE_DATE_RE
from app.parsing.pdf import pnum

def extract_blocks_line_joined_generic(full_text: str) -> list:
    """Join multiline transactions for unknown banks using common date prefixes."""
    blocks = []
    current = []
    for line in full_text.split("\n"):
        stripped = line.strip()
        if GENERIC_LINE_DATE_RE.match(stripped):
            if current:
                blocks.append("\n".join(current))
            current = [stripped]
        elif current:
            current.append(stripped)
    if current:
        blocks.append("\n".join(current))
    return [b for b in blocks if re.findall(r"[\d,]+\.\d{2}", b)]


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

    if bank == "generic":
        blocks = extract_blocks_line_joined_generic(full_text)
        if len(blocks) >= 2:
            return blocks
        splitter = BLOCK_SPLITTERS["generic"]
        blocks = re.split(splitter, full_text)
        return [
            b for b in blocks
            if re.search(r"[\d,]+\.\d{2}", b)
            and GENERIC_LINE_DATE_RE.match(b.strip())
        ]

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
