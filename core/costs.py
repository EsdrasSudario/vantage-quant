import os

COMMISSION_ROUND_TURN = {
    "STANDARD_STP": 0.00,   # custo embutido no spread
    "RAW_ECN":      6.00,   # $3.00/lado × 2
    "PRO_ECN":      3.00,   # $1.50/lado × 2
    "CENT":         0.06,   # RAW_ECN ÷ 100 (lotes em centavos)
}

def get_commission_per_lot() -> float:
    account = os.getenv("ACCOUNT_TYPE", "RAW_ECN")
    return COMMISSION_ROUND_TURN.get(account, 6.00)
