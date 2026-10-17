"""Train and compare demand models; all metrics are computed from the input data."""

import json
from pathlib import Path

import pandas as pd

from ml.demand import evaluate_demand


ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"


def run():
    order_lines = pd.read_csv(ROOT / "data" / "order_lines.csv")
    products = pd.read_csv(ROOT / "data" / "products.csv")
    result = evaluate_demand(order_lines, products)
    RESULTS_DIR.mkdir(exist_ok=True)
    result["metrics"].to_csv(RESULTS_DIR / "demand_metrics.csv", index=False)
    result["predictions"].to_csv(RESULTS_DIR / "demand_predictions.csv", index=False)
    status_rows = json.loads(result["metrics"].to_json(orient="records"))
    metadata = {
        "synthetic_data": True,
        "train_through": str(result["train"]["week"].max().date()),
        "validation_through": str(result["validation"]["week"].max().date()),
        "test_start": str(result["test"]["week"].min().date()),
        "test_end": str(result["test"]["week"].max().date()),
        "validation_mae": result["validation_mae"],
        "results": status_rows,
    }
    (RESULTS_DIR / "demand_metrics.json").write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
    print(result["metrics"].to_string(index=False))
    return result


if __name__ == "__main__":
    run()
