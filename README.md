Fundability Analyser
An AI-powered bank statement analyser that scores UAE SME funding readiness in seconds.

What It Does
Upload a UAE business bank statement PDF (ADCB, Emirates NBD, RAKBank, Wio Bank, and more), enter your AECB credit score and months in business — get back a 0–100 fundability score with key financial metrics, lender-style observations, and an instant readout of where you stand before you walk into any bank.

Stack
Frontend — Vanilla HTML/CSS/JS

Backend — Python · Flask · pypdf

LLM — OpenRouter free models (Hawk-style routing with fallbacks)

PDF Parsing — pypdf + regex (multi-bank UAE statement formats)

Supported Banks
Bank	Header fields	Transaction format
ADCB (detailed export)	Total credits/debits, opening/closing balance	Sr.No + date columns
ADCB (legacy)	Computed from transactions + statement period	DD/MM/YYYY multiline rows
Emirates NBD (ENBD)	Computed from transactions + carried forward balance	DDMMMYY date prefix
RAKBank	Total deposits/withdrawals, opening/closing balance	DD-MMM-YYYY rows
Wio Bank	Opening/closing balance, computed credits/debits	Date + reference + signed amount

Setup
bash
# 1. Install dependencies
pip install flask flask-cors pypdf httpx python-dotenv python-dateutil

# 2. Add OpenRouter keys (reuse OR_KEY_1..5 from Hawk if you have them)
# OR_KEY_1=sk-or-v1-...
# OR_KEY_2=sk-or-v1-...
# Optional throttling (defaults match Hawk free tier):
# OR_MAX_REQUESTS_PER_MINUTE=8
# OR_MIN_REQUEST_INTERVAL_SECONDS=2
# OR_RETRY_DELAY_SECONDS=10

# 3. Start the backend
python backend.py
# Runs on http://localhost:5001

# 4. Open linkit-analyser.html in your browser
How It Works
PDF extraction — pypdf pulls raw text from each page

Bank detection — identifies ADCB, ENBD, RAKBank, Wio, or Mashreq from statement headers

Header parsing — bank-specific regex extracts total credits, debits, closing balance, and statement period

Transaction parsing — splits on each bank's date/row pattern, scans descriptions for lender risk signals

Scoring — rule-based model starting at 50, adjusted by 9 weighted signals

LLM call — one OpenRouter request (free model rotation) with public metrics only; returns verdict, summary, and 4 observations

Response — JSON to frontend; score ring animates, metrics grid renders

Scoring Signals
Signal	Strong	Weak
AECB Score	≥660 → +15	<600 → -15
Monthly Revenue (AED)	≥500K → +15	<30K → -10
Time in Business	≥36mo → +10	<6mo → -10
Closing Balance	≥50K → +5	<10K → -5
Cheque Bounces	0	Each → -8 (max -24)
Cash Deposit %	≤20%	>40% → -10
ATM Withdrawals	0	>15 → -6
Personal Spend Flags	≤15	>80 → -8
Inflow Sources	≥10 → +8	<3 → -5
Score is clamped between 5 and 95.
