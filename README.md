Fundability Analyser
An AI-powered bank statement analyser that scores UAE SME funding readiness in seconds.

What It Does
Upload an ADCB bank statement PDF, enter your AECB credit score and months in business — get back a 0–100 fundability score with key financial metrics, lender-style observations, and an instant readout of where you stand before you walk into any bank.

Stack
Frontend — Vanilla HTML/CSS/JS

Backend — Python · Flask · pypdf

LLM — Anthropic Claude Haiku 4.5

PDF Parsing — pypdf + regex (ADCB statement format)

Setup
bash
# 1. Install dependencies
pip install flask flask-cors pypdf anthropic python-dotenv python-dateutil

# 2. Add your Anthropic key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# 3. Start the backend
python app.py
# Runs on http://localhost:5001

# 4. Open index.html in your browser
How It Works
PDF extraction — pypdf pulls raw text from each page

Header parsing — regex extracts total credits, debits, closing balance, and statement period from page 1

Transaction parsing — splits on ADCB's Sr.No + date pattern, takes first 250 chars per block to avoid column duplication

Scoring — rule-based model starting at 50, adjusted by 9 weighted signals

Claude call — one Haiku call with public metrics only; returns verdict, summary, and 4 observations

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
