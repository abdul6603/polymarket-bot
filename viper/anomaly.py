```python
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("viper.anomaly")

class AnomalyDetector:
    def __init__(self, threshold: float = 0.05):
        self.threshold = threshold
        self.detected_anomalies: List[Dict[str, Any]] = []

    def detect(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Detect anomalies in the provided data.
        Returns anomaly details if found, None otherwise.
        """
        try:
            if not data:
                logger.warning("No data provided for anomaly detection.")
                return None

            # Example logic: Check for missing critical fields or extreme values
            if "value" not in data:
                logger.warning("Missing 'value' field in data.")
                return {"type": "missing_field", "field": "value"}

            value = data.get("value", 0)
            
            # Simple statistical anomaly check
            if abs(value) > self.threshold * 100:
                anomaly = {
                    "type": "statistical_outlier",
                    "value": value,
                    "threshold": self.threshold,
                    "timestamp": self._get_timestamp()
                }
                self.detected_anomalies.append(anomaly)
                logger.warning(f"Anomaly detected: {anomaly}")
                return anomaly

            return None

        except Exception as e:
            logger.error(f"Error during anomaly detection: {e}", exc_info=True)
            return {"type": "detection_error", "message": str(e)}

    def _get_timestamp(self) -> str:
        from datetime import datetime
        return datetime.utcnow().isoformat()

    def get_history(self) -> List[Dict[str, Any]]:
        return self.detected_anomalies

def run_anomaly_check(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    detector = AnomalyDetector()
    return detector.detect(data)
```