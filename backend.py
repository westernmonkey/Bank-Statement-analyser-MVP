import os
import re
import json
from datetime import datetime
from dateutil.relativedelta import relativedelta
import anthropic
from flask import Flask, request, jsonify
from flask_cors import CORS
from pypdf import PdfReader
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


# ── HELPERS ─────────────────────────────────────────────────────

def pnum(text: str, pattern: str) -> float:
    """Extract a single number from text using regex."""
    m = re.search(pattern, text, re.IGNORECASE)
    return float(m.group(1).replace(",", "")) if m else 0.0


def extract_pages(file) -> list:
    """Return list of raw text strings, one per page."""
    reader = PdfReader(file)
    return [(page.extract_text() or "") for page in reader.pages]


def extract_transaction_blocks(pages: list) -> list:
    """
    Join all pages, split into per-transaction blocks.
    Each block starts with Sr.No + date line.
    Take only first 250 chars per block (Description column only,
    before ADCB repeats the text in Payment Details column).
    """
    full_text = "\n".join(pages)
    blocks = re.split(r'\n\s*\d+\s+\d{2}-\w{3}-\n?\d{4}', full_text)
    return [b[:250] for b in blocks[1:]]  # skip pre-transaction header block


# ── PAGE 1 PARSER (header summary fields) ───────────────────────

def parse_header(page1: str) -> dict:
    """
    Parse all aggregate fields from the ADCB statement header on page 1.
    These are exact numbers — no estimation needed.
    """
    total_credit  = pnum(page1, r"Total Credit Amount[:\s]+([\d,]+\.?\d*)")
    total_debit   = pnum(page1, r"Total Debit Amount[:\s]+([\d,]+\.?\d*)")
    closing_bal   = pnum(page1, r"Closing\(Available\) Balance[:\s]+([\d,]+\.?\d*)")
    opening_bal   = pnum(page1, r"Opening Balance[:\s]+([\d,]+\.?\d*)")
    num_credits   = int(pnum(page1, r"Total no of credits[:\s]+(\d+)"))
    num_debits    = int(pnum(page1, r"Total no of debits[:\s]+(\d+)"))
    avg_balance   = pnum(page1, r"Average Balance[:\s]+([\d,]+\.?\d*)")

    # Derive statement period from start/end dates
    start_m = re.search(r"Start Date[:\s]+(\d{2}-\w+-\d{4})", page1, re.IGNORECASE)
    end_m   = re.search(r"End Date[:\s]+(\d{2}-\w+-\d{4})", page1, re.IGNORECASE)
    period_months = 6  # default
    if start_m and end_m:
        try:
            start_dt = datetime.strptime(start_m.group(1), "%d-%b-%Y")
            end_dt   = datetime.strptime(end_m.group(1), "%d-%b-%Y")
            diff = relativedelta(end_dt, start_dt)
            period_months = max(1, diff.months + diff.years * 12)
        except Exception:
            period_months = 6

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


# ── TRANSACTION PARSER (pages 2-end) ────────────────────────────

def parse_transactions(blocks: list, total_credit: float) -> dict:
    """
    Count transaction-level signals from per-block descriptions.
    One block = one transaction. Avoids ADCB column duplication.
    """

    # Cheque bounces: only CHQ RETRN = actual bounce event
    # "Cheque Return Charge" = fee line, excluded intentionally
    bounces = sum(
        1 for b in blocks
        if re.search(r"CHQ RETRN|Insufficient Funds", b, re.IGNORECASE)
    )

    # Inward cheque bounces specifically (bounced cheques received)
    inward_bounces = sum(
        1 for b in blocks
        if re.search(r"CHQ RETRN", b, re.IGNORECASE)
    )

    # CDM = Cash Deposit Machine (cash deposits, unlinkable source)
    cdm_blocks = [b for b in blocks if re.search(r"CDM-Cash Deposit", b, re.IGNORECASE)]
    cdm_count  = len(cdm_blocks)

    # Sum CDM amounts from blocks — pattern: amount after " - " or credit column
    cdm_total = 0.0
    for b in cdm_blocks:
        amounts = re.findall(r"[\-\s]([\d,]+\.\d{2})\s", b)
        for a in amounts:
            val = float(a.replace(",", ""))
            if 100 < val < 500000:  # sanity range
                cdm_total += val
                break  # one amount per block

    cash_deposit_pct = round(cdm_total / total_credit * 100) if total_credit else 0

    # ATM withdrawals
    atm_count = sum(1 for b in blocks if re.search(r"ATM WDL", b, re.IGNORECASE))

    # Personal spend: POS card purchases (PUR prefix) and known personal brands
    pur_count = sum(1 for b in blocks if re.search(r"^[\s\S]{0,60}\bPUR\b", b, re.IGNORECASE))

    personal_brands = re.compile(
        r"\bAPPLE\b|\bAMAZON\b|\bNOON\b|\bLINKEDIN\b|\bMAKEMYTRIP\b"
        r"|\bPARKONIC\b|\bCARREFOUR\b|\bTALABAT\b|\bNETFLIX\b|\bSPOTIFY\b"
        r"|\bIKEA\b|\bSOUVENIR\b|\bQATAR AIR\b|\bGIORDANO\b|\bCOCONUT\b"
        r"|\bHAYYA\b|\bSANGEETHA\b|\bNASEAM\b",
        re.IGNORECASE
    )
    brand_count = sum(1 for b in blocks if personal_brands.search(b))

    # ADNOC: half weight (legitimate fuel purchases vs personal petrol — ambiguous)
    adnoc_count = sum(1 for b in blocks if re.search(r"\bADNOC\b", b, re.IGNORECASE))

    personal_spend_flags = pur_count + brand_count + (adnoc_count // 2)

    # Unique inflow sources (B/O_ pattern = incoming wire transfers)
    remitter_set = set()
    for b in blocks:
        m = re.search(r"B/O[_\s]+([A-Z][A-Z0-9\s\-&\.]{3,39}?)[\n\r]", b)
        if m:
            remitter_set.add(m.group(1).strip())
    unique_inflow_sources = len(remitter_set)

    # NI POS = Network International POS settlements (card terminal revenue)
    ni_pos_count = sum(1 for b in blocks if re.search(r"NI POS|NETWORK IN", b, re.IGNORECASE))

    # Cheque deposits received
    cheque_deposits_in = sum(
        1 for b in blocks
        if re.search(r"CHEQUE DEPOSIT|INHOUSE CHEQUE DEPOSIT", b, re.IGNORECASE)
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


def call_claude(metrics: dict, score: int) -> dict:
    verdict = "Strong" if score >= 70 else "Moderate" if score >= 45 else "Needs Work"

    # Only send safe public fields to Claude — private signals stay server-side
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

    payload = f"Score: {score}/100\nVerdict: {verdict}\nMetrics:\n{json.dumps(public, indent=2)}"

    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": payload}]
    )

    raw = re.sub(r"```json|```", "", resp.content[0].text).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "verdict": verdict,
            "sub": "Analysis complete. Review metrics below.",
            "points": [
                f"Monthly revenue of {metrics.get('avg_monthly_revenue', 0):,} AED extracted from statement.",
                f"AECB score of {metrics.get('aecb', 0)} noted.",
                f"Closing balance of {metrics.get('closing_balance', 0):,} AED recorded.",
                f"Business operating for {metrics.get('months_in_business', 0)} months."
            ]
        }


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

        # Parse header (page 1)
        header = parse_header(pages[0])

        if header["total_credits_6m"] == 0:
            return jsonify({"error": "Could not extract financial data from page 1. Make sure this is an ADCB bank statement."}), 400

        # Parse transactions (all pages for transaction signals)
        blocks       = extract_transaction_blocks(pages)
        transactions = parse_transactions(blocks, header["total_credits_6m"])

        # Merge all metrics
        metrics = {
            "aecb":             aecb,
            "months_in_business": months,
            **header,
            **transactions,
        }

        # Score
        score = compute_score(metrics)

        # Single LLM call with aggregated metrics
        result = call_claude(metrics, score)

        return jsonify({
            "score":   score,
            "metrics": metrics,
            "verdict": result.get("verdict", "Moderate"),
            "sub":     result.get("sub", ""),
            "points":  result.get("points", []),
        })

    except Exception as e:
        return jsonify({
            "error": f"Unexpected server error: {str(e)}. Please try again or contact support."
        }), 500


if __name__ == "__main__":
    app.run(debug=True, port=5001)