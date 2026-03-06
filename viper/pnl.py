```python
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger("viper.pnl")

class PnLCalculator:
    def __init__(self):
        self.history: List[Dict[str, Any]] = []

    def calculate(self, trades: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Calculate Profit and Loss from a list of trades.
        Handles data parsing errors and API failures gracefully.
        """
        try:
            if not trades:
                logger.warning("No trades provided for PnL calculation.")
                return {"status": "no_data"}

            total_pnl = 0.0
            trade_count = 0
            errors = []

            for trade in trades:
                try:
                    # Validate trade structure
                    if "entry" not in trade or "exit" not in trade:
                        logger.warning(f"Invalid trade structure: {trade}")
                        continue

                    entry_price = float(trade.get("entry", 0))
                    exit_price = float(trade.get("exit", 0))
                    quantity = float(trade.get("quantity", 1))
                    
                    # Calculate individual trade PnL
                    trade_pnl = (exit_price - entry_price) * quantity
                    total_pnl += trade_pnl
                    trade_count += 1

                except (ValueError, TypeError) as e:
                    errors.append(f"Trade parsing error: {e}")
                    continue

            result = {
                "total_pnl": total_pnl,
                "trade_count": trade_count,
                "timestamp": datetime.utcnow().isoformat(),
                "errors": errors if errors else None
            }

            self.history.append(result)
            logger.info(f"PnL calculated: {total_pnl:.2f} over {trade_count} trades.")
            return result

        except Exception as e:
            logger.critical(f"Critical failure in PnL calculation: {e}", exc_info=True)
            return {"status": "critical_error", "message": str(e)}

    def get_history(self) -> List[Dict[str, Any]]:
        return self.history

def calculate_pnl(trades: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    calculator = PnLCalculator()
    return calculator.calculate(trades)
```