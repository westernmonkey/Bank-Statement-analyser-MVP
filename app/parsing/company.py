"""Company name and statement period extraction."""
import re

ENTITY_SUFFIX_RE = re.compile(
    r"(?:FZCO|FZE|FZC|L\.L\.C|LLC|LTD|CO\s+LLC|TRADING|GENERAL\s+TRADING|CONTRACTING|SERVICES)",
    re.IGNORECASE,
)
SKIP_LINE_RE = re.compile(
    r"^(Account|IBAN|Statement|Summary|Report|Page|Date|Customer|Please|Total|Opening|Closing|"
    r"ACCOUNT\s+STATEMENT|CURRENT|Emirates\s+NBD|RAKBANK|Wio\s+Bank|Mashreq|ADCB|"
    r"SR\.?\s*NO|DESCRIPTION|DEBIT|CREDIT|BALANCE|Summary\s+of\s+Accounts)$",
    re.IGNORECASE,
)


def _company_profile_result(name: str, address_lines: list[str]) -> dict:
    name = re.sub(r"\s+", " ", name.strip())
    address_lines = [re.sub(r"\s+", " ", ln.strip()) for ln in address_lines if ln.strip()]
    full_block = name
    if address_lines:
        full_block = name + "\n" + "\n".join(address_lines)
    return {
        "name": name,
        "address_lines": address_lines,
        "full_block": full_block,
    }


def _extract_company_wio(page1: str) -> dict | None:
    m = re.search(
        r"^([A-Z0-9][A-Z0-9\s\-&\.']+(?:FZCO|FZE|FZC|LLC|LTD|L\.L\.C|TRADING)[^\n]*)\n"
        r"((?:.*\n)+?)"
        r"(?:ACCOUNT STATEMENT|FROM\s+\d{2}/\d{2}/\d{4})",
        page1,
        re.IGNORECASE | re.MULTILINE,
    )
    if not m:
        m = re.search(
            r"^([A-Z][A-Z0-9\s\-&\.']+)\n((?:.+\n)+?)ACCOUNT STATEMENT",
            page1,
            re.MULTILINE,
        )
    if not m:
        return None
    name = m.group(1).strip()
    raw_lines = [ln.strip() for ln in m.group(2).split("\n") if ln.strip()]
    address_lines = [
        ln for ln in raw_lines
        if ln != name
        and not SKIP_LINE_RE.match(ln)
        and not re.match(r"^\s*FROM\s+\d", ln, re.I)
    ]
    return _company_profile_result(name, address_lines)


def _extract_company_adcb_detailed(page1: str) -> dict | None:
    m = re.search(
        r"Account\s+Name\s*:\s*(.+?)(?:\s+IBAN|\s+Average|\n)",
        page1,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(r"AED\s+Account\s+Name\s*:\s*(.+)", page1, re.IGNORECASE)
    if not m:
        return None
    return _company_profile_result(m.group(1).strip(), [])


def _extract_company_adcb_legacy(page1: str) -> dict | None:
    before_acct = page1.split("Account Number", 1)[0]
    for line in before_acct.split("\n"):
        stripped = line.strip()
        if len(stripped) > 5 and ENTITY_SUFFIX_RE.search(stripped) and not SKIP_LINE_RE.match(stripped):
            return _company_profile_result(stripped, [])
    return None


def _extract_company_enbd(page1: str) -> dict | None:
    m = re.search(r"M/S\.?\s*(.+?)(?:\n|CURRENT\s+ACCOUNT)", page1, re.IGNORECASE)
    if m:
        return _company_profile_result(m.group(1).strip(), [])
    return None


def _extract_company_rak(page1: str) -> dict | None:
    for line in page1.split("\n"):
        stripped = line.strip()
        if not stripped or SKIP_LINE_RE.match(stripped):
            continue
        if "Account Type" in stripped or "IBAN" in stripped:
            break
        if ENTITY_SUFFIX_RE.search(stripped) and len(stripped) > 5:
            name = re.sub(r"[^\x00-\x7F]+", "", stripped).strip()
            if name:
                return _company_profile_result(name, [])
    return None


def _extract_company_generic(page1: str) -> dict | None:
    head = page1[:2500]
    best = None
    for line in head.split("\n"):
        stripped = line.strip()
        if len(stripped) < 5 or SKIP_LINE_RE.match(stripped):
            continue
        if ENTITY_SUFFIX_RE.search(stripped):
            if not best or len(stripped) > len(best):
                best = stripped
    if best:
        return _company_profile_result(best, [])
    m = re.search(
        r"(?:Account\s+Name|M/S\.?|Customer\s+Name)\s*:?\s*(.+)",
        head,
        re.IGNORECASE,
    )
    if m:
        return _company_profile_result(m.group(1).strip(), [])
    return None


def extract_company_profile(full_text: str, pages: list, bank_id: str) -> dict:
    """Extract company name and address block from statement header (admin-only)."""
    page1 = pages[0] if pages else ""
    extractors = {
        "wio":           lambda: _extract_company_wio(page1),
        "adcb_detailed": lambda: _extract_company_adcb_detailed(page1),
        "adcb_legacy":   lambda: _extract_company_adcb_legacy(page1),
        "enbd":          lambda: _extract_company_enbd(page1),
        "rak":           lambda: _extract_company_rak(page1),
    }
    result = extractors.get(bank_id, lambda: None)()
    if not result:
        result = _extract_company_generic(page1)
    if not result:
        return {"name": "", "address_lines": [], "full_block": ""}
    return result


def extract_statement_period(full_text: str, pages: list, bank_id: str) -> dict:
    """Extract statement start/end dates and a display label (per-bank regex)."""
    page1 = pages[0] if pages else ""
    start = end = ""

    if bank_id == "adcb_detailed":
        start_m = re.search(r"Start Date[:\s]+(\d{2}-\w+-\d{4})", page1, re.IGNORECASE)
        end_m = re.search(r"End Date[:\s]+(\d{2}-\w+-\d{4})", page1, re.IGNORECASE)
        if start_m:
            start = start_m.group(1)
        if end_m:
            end = end_m.group(1)

    elif bank_id in ("adcb_legacy", "adcb"):
        period_m = re.search(
            r"Statement Period\s+(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})",
            page1, re.IGNORECASE,
        )
        if period_m:
            start, end = period_m.group(1), period_m.group(2)

    elif bank_id == "enbd":
        period_m = re.search(
            r"Statement Period\s+From\s+(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})",
            full_text, re.IGNORECASE | re.DOTALL,
        )
        if not period_m:
            period_m = re.search(
                r"Statement Period[\s\S]{0,3000}?(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})",
                full_text, re.IGNORECASE,
            )
        if period_m:
            start, end = period_m.group(1), period_m.group(2)

    elif bank_id == "rak":
        period_m = re.search(
            r"Statement Period:\s+(\d{2}-\w+-\d{4})\s+to\s+(\d{2}-\w+-\d{4})",
            page1, re.IGNORECASE,
        )
        if period_m:
            start, end = period_m.group(1), period_m.group(2)

    elif bank_id == "wio":
        period_m = re.search(
            r"FROM\s+(\d{2}/\d{2}/\d{4})\s+TO\s+(\d{2}/\d{2}/\d{4})",
            full_text, re.IGNORECASE,
        )
        if period_m:
            start, end = period_m.group(1), period_m.group(2)

    if not start or not end:
        for pat in (
            r"Start Date[:\s]+(\d{2}-\w+-\d{4})[\s\S]{0,120}?End Date[:\s]+(\d{2}-\w+-\d{4})",
            r"Statement Period:\s+(\d{2}-\w+-\d{4})\s+to\s+(\d{2}-\w+-\d{4})",
            r"Statement Period\s+(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})",
            r"FROM\s+(\d{2}/\d{2}/\d{4})\s+TO\s+(\d{2}/\d{2}/\d{4})",
            r"Statement Period[\s\S]{0,3000}?(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})",
        ):
            m = re.search(pat, full_text, re.IGNORECASE)
            if m:
                start, end = m.group(1), m.group(2)
                break

    display = f"{start} – {end}" if start and end else ""
    return {"start": start, "end": end, "display": display}
