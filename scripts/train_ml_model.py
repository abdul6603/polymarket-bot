"""Standalone training script for Garves ML Win Predictor.

Usage:
    cd ~/polymarket-bot && .venv/bin/python scripts/train_ml_model.py
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.ml_predictor import GarvesMLPredictor


def main():
    print("=" * 60)
    print("GARVES ML WIN PREDICTOR — Training")
    print("=" * 60)

    metrics = GarvesMLPredictor.train()

    status = metrics.get("status", "unknown")
    print(f"\nStatus: {status}")
    print(f"Training samples: {metrics.get('num_samples', 0)}")

    if status == "insufficient_data":
        print(f"Need at least {metrics.get('min_required', 30)} resolved trades.")
        print("Keep trading and try again later.")
        return

    print(f"Base win rate: {metrics['win_rate']:.1%}")
    print(f"Model accuracy (train): {metrics['accuracy']:.1%}")
    print(f"CV accuracy (5-fold): {metrics['cv_accuracy']:.1%} (±{metrics.get('cv_std', 0):.1%})")
    print(f"Precision: {metrics['precision']:.3f}")
    print(f"Recall: {metrics['recall']:.3f}")
    print(f"F1 Score: {metrics['f1']:.3f}")

    print(f"\nTop 15 Feature Importances:")
    for name, imp in metrics.get("top_features", [])[:15]:
        bar = "█" * int(imp * 200)
        print(f"  {name:30s} {imp:.4f} {bar}")

    print(f"\nModel saved to: {metrics.get('model_path', 'N/A')}")
    print("Done.")


if __name__ == "__main__":
    main()
