```python
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("PnLCalculator")

def calculate_pnl() -> Optional[Dict[str, Any]]:
    """
    Calculates Profit and Loss.
    Wraps logic in try-except to handle data parsing errors or connection timeouts.
    """
    try:
        logger.debug("Calculating PnL...")
        
        # Simulate fetching trade data
        trades = _fetch_trades()
        
        if not trades:
            logger.warning("No trades found to calculate PnL.")
            return None

        total_pnl = 0.0
        trade_count = 0
        
        for trade in trades:
            try:
                # Safe division and calculation
                entry_price = float(trade.get("entry_price", 0))
                exit_price = float(trade.get("exit_price", 0))
                quantity = float(trade.get("quantity", 0))
                
                if entry_price == 0:
                    continue
                    
                pnl = (exit_price - entry_price) * quantity
                total_pnl += pnl
                trade_count += 1
                
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid trade data format: {e}. Skipping trade.")
                continue

        result = {
            "total_pnl": total_pnl,
            "trade_count": trade_count,
            "avg_pnl": total_pnl / trade_count if trade_count > 0 else 0.0
        }
        
        logger.info(f"PnL Calculation complete: {result}")
        return result

    except Exception as e:
        logger.error(f"Critical error in PnL calculation: {e}")
        return None

def _fetch_trades() -> Optional[list]:
    """
    Simulates fetching trade history.
    """
    # Placeholder implementation
    return [
        {"entry_price": 100.0, "exit_price": 105.0, "quantity": 10},
        {"entry_price": 110.0, "exit_price": 108.0, "quantity": 5}
    ]