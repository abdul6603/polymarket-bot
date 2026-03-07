```python
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("PnL_Calculator")

def calculate_pnl(market_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Calculate Profit and Loss.
    Handles missing data gracefully to prevent crashes.
    """
    default_response = {
        "valid": False,
        "pnl": 0.0,
        "entry_price": 0.0,
        "exit_price": 0.0,
        "error": "No market data provided."
    }
    
    if market_data is None:
        logger.warning("No market data provided. Returning default PnL state.")
        return default_response

    try:
        entry_price = float(market_data.get("entry_price", 0))
        exit_price = float(market_data.get("exit_price", 0))
        quantity = float(market_data.get("quantity", 1))
        
        if entry_price <= 0 or exit_price <= 0:
            logger.warning("Invalid price values provided.")
            return default_response
            
        pnl = (exit_price - entry_price) * quantity
        
        return {
            "valid": True,
            "pnl": pnl,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity
        }
        
    except (ValueError, TypeError) as e:
        logger.error(f"Failed to parse PnL data: {e}")
        return default_response
    except Exception as e:
        logger.error(f"Unexpected error in PnL calculation: {e}", exc_info=True)
        return default_response
```