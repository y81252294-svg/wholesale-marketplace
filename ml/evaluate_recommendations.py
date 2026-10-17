"""Run time-based recommendation evaluation and persist measured metrics."""

import json
from pathlib import Path

import pandas as pd

from ml.recommendation import HybridRecommender, choose_alpha, ranking_metrics


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"


def run(k: int = 10):
    products = pd.read_csv(DATA_DIR / "products.csv")
    lines = pd.read_csv(DATA_DIR / "order_lines.csv", parse_dates=["date"])
    lines["date"] = pd.to_datetime(lines["date"], utc=True).dt.tz_convert(None)
    dates = sorted(lines["date"].dt.to_period("W-SUN").unique())
    if len(dates) < 6:
        raise ValueError("Recommendation evaluation requires at least six weeks of orders.")
    train_cutoff = dates[int(len(dates) * 0.7)]
    validation_cutoff = dates[int(len(dates) * 0.85)]
    weeks = lines["date"].dt.to_period("W-SUN")
    train = lines[weeks <= train_cutoff].copy()
    validation = lines[(weeks > train_cutoff) & (weeks <= validation_cutoff)].copy()
    test = lines[weeks > validation_cutoff].copy()
    retailer_categories = {}
    alpha = choose_alpha(products, train, validation, retailer_categories)
    hybrid = HybridRecommender(alpha=alpha).fit(products, train, retailer_categories)
    popularity = train.groupby("product_id")["quantity"].sum().sort_values(ascending=False).index.tolist()
    truth = test.groupby("retailer_id")["product_id"].apply(set).to_dict()
    rows = []
    for name, model in (
        ("hybrid", hybrid),
        ("popularity", None),
    ):
        measurements = []
        for retailer_id, actual in truth.items():
            predicted = (
                [entry["product_id"] for entry in model.recommend(int(retailer_id), k)]
                if model is not None
                else [int(product_id) for product_id in popularity if product_id not in set(
                    train.loc[train["retailer_id"] == retailer_id, "product_id"]
                )][:k]
            )
            measurements.append(ranking_metrics(actual, predicted, k))
        rows.append(
            {
                "model": name,
                "k": k,
                **{
                    metric: float(pd.Series([row[metric] for row in measurements]).mean())
                    for metric in ("precision_at_k", "recall_at_k", "f1_at_k", "ndcg_at_k")
                },
                "retailers_evaluated": len(measurements),
            }
        )
    RESULTS_DIR.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(RESULTS_DIR / "recommendation_metrics.csv", index=False)
    summary = {
        "synthetic_data": True,
        "split": {
            "train_through": str(train_cutoff),
            "validation_through": str(validation_cutoff),
            "test_after": str(validation_cutoff),
        },
        "selected_alpha": alpha,
        "k": k,
        "results": rows,
    }
    (RESULTS_DIR / "recommendation_metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(pd.DataFrame(rows).to_string(index=False))
    return rows


if __name__ == "__main__":
    run()
