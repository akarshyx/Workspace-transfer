from main import user_balances, save_data

async def get_balance(user_id):
    """Get user balance from main bot's user_balances."""
    uid = str(user_id)
    return float(user_balances.get(uid, 0.0))

async def wallet_debit(user_id, amount, reason):
    """Deduct balance from main bot's user_balances."""
    uid = str(user_id)
    current_balance = float(user_balances.get(uid, 0.0))
    if current_balance < amount:
        raise ValueError("Insufficient balance")
    
    user_balances[uid] = current_balance - amount
    try:
        save_data()
    except Exception as e:
        print(f"Error saving data in wallet_debit: {e}")
    return True

async def wallet_credit(user_id, amount, reason):
    """Add balance to main bot's user_balances."""
    uid = str(user_id)
    current_balance = float(user_balances.get(uid, 0.0))
    user_balances[uid] = current_balance + amount
    try:
        save_data()
    except Exception as e:
        print(f"Error saving data in wallet_credit: {e}")
    return True
