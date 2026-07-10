"""LLM narrative generation for analysis results."""
import json
import re

from app.services.llm import call_openrouter

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
