"""Rule-based fundability scoring."""

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
