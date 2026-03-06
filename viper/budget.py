```python
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("BudgetManager")

def manage_budget(decision: Optional[Dict[str, str]]) -> None:
    """
    Manages budget state based on decisions.
    Ensures state transitions are atomic or wrapped in error handling.
    """
    try:
        logger.debug("Managing budget...")
        
        if not decision:
            logger.info("No decision provided. Skipping budget update.")
            return

        action = decision.get("action", "")
        reason = decision.get("reason", "")
        
        # Simulate budget update logic
        current_budget = _get_current_budget()
        
        if action == "REDUCE_EXPOSURE":
            logger.info(f"Reducing exposure due to: {reason}")
            # Logic to reduce position size
            _update_budget(current_budget, -0.1)
            
        elif action == "HOLD":
            logger.info(f"Holding position. Reason: {reason}")
            # Logic to maintain current state
            
        else:
            logger.warning(f"Unknown action: {action}")

    except Exception as e:
        logger.error(f"Budget management failed: {e}")
        # Fallback: Log error and do not crash the agent

def _get_current_budget() -> float:
    """
    Simulates fetching current budget.
    """
    return 10000.0

def _update_budget(current: float, change: float) -> float:
    """
    Simulates updating budget.
    """
    new_budget = current + change
    logger.info(f"Budget updated: {current} -> {new_budget}")
    return new_budget