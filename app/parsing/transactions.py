"""Transaction signal detection."""
import re

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
