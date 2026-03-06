```python
import logging
import sys
import time
from typing import Optional, Dict, Any

# Import local modules
from viper.anomaly import detect_anomalies
from viper.pnl import calculate_pnl
from viper.brain import make_decision
from viper.budget import manage_budget

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/Users/macuser/polymarket-bot/viper/agent.log')
    ]
)
logger = logging.getLogger("ViperAgent")

class ViperAgent:
    """
    The core agent loop for the Viper system.
    Handles connection stability, error recovery, and module orchestration.
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.is_running = False
        self.retry_count = 0
        self.max_retries = 3
        self.backoff_factor = 2.0

    def run_loop(self) -> None:
        """
        The main execution loop. Wraps all external calls in try-except blocks.
        Catches ConnectionError and generic exceptions to prevent crashes.
        """
        self.is_running = True
        logger.info("ViperAgent starting main loop...")

        while self.is_running:
            try:
                self._execute_cycle()
                self.retry_count = 0  # Reset on success
                time.sleep(5)  # Standard polling interval

            except ConnectionError as e:
                logger.critical(f"Connection Error detected: {e}. Initiating recovery.")
                self._handle_connection_recovery()
            except Exception as e:
                logger.exception(f"Critical unhandled exception in main loop: {e}")
                self._handle_critical_error(e)

    def _execute_cycle(self) -> None:
        """
        Executes the core logic: Anomaly -> PnL -> Brain -> Budget.
        """
        logger.debug("Executing agent cycle...")

        # 1. Anomaly Detection
        anomalies = detect_anomalies()
        if anomalies:
            logger.warning(f"Anomalies detected: {anomalies}")

        # 2. PnL Calculation
        pnl_data = calculate_pnl()
        if pnl_data:
            logger.info(f"PnL calculated: {pnl_data}")

        # 3. Decision Making
        decision = make_decision(pnl_data, anomalies)
        if decision:
            logger.info(f"Decision made: {decision}")

        # 4. Budget Management
        manage_budget(decision)

    def _handle_connection_recovery(self) -> None:
        """
        Implements exponential backoff for connection errors.
        """
        if self.retry_count >= self.max_retries:
            logger.error("Max retries reached. Shutting down gracefully.")
            self.is_running = False
            return

        wait_time = 2 ** self.retry_count
        logger.info(f"Waiting {wait_time}s before retry...")
        time.sleep(wait_time)
        self.retry_count += 1

    def _handle_critical_error(self, error: Exception) -> None:
        """
        Handles generic exceptions that are not ConnectionErrors.
        Logs stack trace and attempts recovery.
        """
        logger.error(f"Critical Error: {str(error)}")
        # In a real scenario, we might trigger a hot-reload or alert here.
        # For now, we log and continue to prevent total system failure.
        time.sleep(1)

    def stop(self) -> None:
        """
        Graceful shutdown method.
        """
        logger.info("Stopping ViperAgent...")
        self.is_running = False

if __name__ == "__main__":
    agent = ViperAgent()
    try:
        agent.run_loop()
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        agent.stop()
    finally:
        logger.info("ViperAgent terminated.")