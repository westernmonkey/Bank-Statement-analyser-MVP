"""Bank detection and display labels."""
import re

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
    return "generic"


def infer_bank_display_name(full_text: str) -> str:
    """Best-effort bank label when no dedicated parser is matched."""
    for pattern, label in BANK_NAME_PATTERNS:
        if re.search(pattern, full_text, re.IGNORECASE):
            return label
    return "Bank Statement"


BANK_LABELS = {
    "adcb_detailed": "ADCB",
    "adcb_legacy":   "ADCB",
    "enbd":          "Emirates NBD",
    "rak":           "RAKBank",
    "wio":           "Wio Bank",
    "mashreq":       "Mashreq",
    "generic":       "Bank Statement",
}


def bank_display_name(bank_id: str, full_text: str) -> str:
    if bank_id == "generic":
        return infer_bank_display_name(full_text)
    return BANK_LABELS.get(bank_id, infer_bank_display_name(full_text))
