```python
import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger("viper.budget")

class BudgetManager:
    def __init__(self, initial_balance: float = 10000.0):
        self.balance = initial_balance
        self.max_drawdown = 0.20  # 20% max drawdown
        self.state_history: List[Dict[str, Any]] = []

    def update(self, pnl_change: float) -> Optional[Dict[str, Any]]:
        """
        Update budget with new PnL change.
        Validates state and prevents crashes on invalid data.
        """
        try:
            if not isinstance(pnl_change, (int, float)):
                raise ValueError(f"Invalid PnL change type: {type(pnl_change)}")

            new_balance = self.balance + pnl_change
            
            if new_balance < 0:
                logger.warning(f"Balance went negative: {new_balance}. Resetting to 0.")
                new_balance = 0.0

            # Check drawdown
            max_loss = self.balance * self.max_drawdown
            if self.balance - new_balance > max_loss:
                logger.critical(f"Max drawdown exceeded! Current: {self.balance}, New: {new_balance}")
                # Trigger emergency stop or alert
                return {"status": "drawdown_exceeded", "balance": new_balance}

            self.balance = new_balance
            record = {
                "timestamp": datetime.utcnow().isoformat(),
                "previous_balance": self.balance - pnl_change,
                "pnl_change": pnl_change,
                "new_balance": new_balance,
                "status": "updated"
            }
            self.state_history.append(record)
            logger.info(f"Budget updated: {new_balance:.2f}")
            return record

        except ValueError as e:
            logger.error(f"Budget validation error: {e}")
            return {"status": "validation_failed", "message": str(e)}
        except Exception as e:
            logger.critical(f"Unexpected budget error: {e}", exc_info=True)
            return {"status": "critical_error", "message": str(e)}

    def get_balance(self) -> float:
        return self.balance

    def get_history(self) -> List[Dict[str, Any]]:
        return self.state_history

def update_budget(pnl_change: float, initial_balance: float = 10000.0) -> Optional[Dict[str, Any]]:
    manager = BudgetManager(initial_balance)
    return manager.update(pnl_change)
```