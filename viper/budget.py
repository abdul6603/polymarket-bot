```python
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("BudgetManager")

def check_budget(wallet_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Check if trading is allowed based on budget.
    Returns a safe state if wallet state is inaccessible.
    """
    default_response = {
        "can_trade": False,
        "available_balance": 0.0,
        "error": "No wallet state provided."
    }
    
    if wallet_state is None:
        logger.warning("No wallet state provided. Trading disabled.")
        return default_response

    try:
        balance = float(wallet_state.get("available_balance", 0))
        min_balance = float(wallet_state.get("min_balance_required", 100.0))
        
        if balance < min_balance:
            logger.info(f"Balance ({balance}) below minimum required ({min_balance}). Trading disabled.")
            return {
                "can_trade": False,
                "available_balance": balance,
                "error": "Insufficient balance."
            }
            
        return {
            "can_trade": True,
            "available_balance": balance,
            "error": None
        }
        
    except (ValueError, TypeError) as e:
        logger.error(f"Failed to parse budget data: {e}")
        return default_response
    except Exception as e:
        logger.error(f"Unexpected error in budget check: {e}", exc_info=True)
        return default_response
```