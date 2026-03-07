```python
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("AnomalyDetector")

def detect_anomalies(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Detect anomalies in market data.
    Returns a safe default state if data is missing or malformed.
    """
    default_response = {
        "is_anomalous": False,
        "details": "No anomalies detected or data unavailable.",
        "confidence": 0.0
    }
    
    if data is None:
        logger.warning("No data provided to anomaly detection. Returning safe state.")
        return default_response

    try:
        # Simulate anomaly detection logic
        # In production, this would check volatility, volume spikes, etc.
        volatility = data.get("volatility", 0.0)
        volume_spike = data.get("volume_spike", False)
        
        if volatility > 5.0 or volume_spike:
            logger.info(f"Anomaly detected: Volatility={volatility}, VolumeSpike={volume_spike}")
            return {
                "is_anomalous": True,
                "details": f"High volatility ({volatility}) or volume spike detected.",
                "confidence": 0.9
            }
            
        return {
            "is_anomalous": False,
            "details": "Market conditions normal.",
            "confidence": 1.0
        }
        
    except KeyError as e:
        logger.error(f"Missing key in anomaly data: {e}")
        return default_response
    except Exception as e:
        logger.error(f"Unexpected error in anomaly detection: {e}", exc_info=True)
        return default_response
```