import numpy as np
import pandas as pd

from ml.metrics import regression_metrics
from ml.inventory_planning import inventory_plan
from ml.preprocessing import add_demand_features, aggregate_weekly_demand, chronological_split
from ml.recommendation import HybridRecommender, ranking_metrics


def test_weekly_aggregation_keeps_zero_demand_weeks():
    lines = pd.DataFrame(
        {
            "product_id": [7, 7, 8],
            "date": ["2025-01-06", "2025-01-20", "2025-01-13"],
            "quantity": [5, 3, 2],
        }
    )
    products = pd.DataFrame({"product_id": [7, 8, 9], "unit_price": [2.5, 3.0, 1.0]})
    result = aggregate_weekly_demand(lines, products)
    p7_middle_week = result[(result.product_id == 7) & (result.week == pd.Timestamp("2025-01-13"))]
    p9 = result[result.product_id == 9]
    assert p7_middle_week.quantity.tolist() == [0.0]
    assert len(p9) == 3
    assert p9.quantity.sum() == 0


def test_features_use_past_values_and_split_by_chronological_week():
    weeks = pd.date_range("2025-01-06", periods=20, freq="W-MON")
    weekly = pd.DataFrame({"product_id": 1, "week": weeks, "quantity": np.arange(20, dtype=float)})
    features = add_demand_features(weekly, lags=(1, 2), rolling_windows=(3,))
    assert features.loc[5, "lag_1"] == 4
    assert features.loc[5, "rolling_mean_3"] == 3
    train, validation, test = chronological_split(features, 0.6, 0.2)
    assert train.week.max() < validation.week.min()
    assert validation.week.max() < test.week.min()
    assert len(train) + len(validation) + len(test) == len(features)


def test_regression_metrics_report_mape_exclusions_for_zero_actuals():
    metrics = regression_metrics([0, 2, 4], [100, 3, 2])
    assert metrics["mape"] == 50.0
    assert metrics["mape_excluded_points"] == 1
    assert metrics["n_points"] == 3
    assert metrics["mae"] > 0


def test_mape_is_null_when_every_actual_is_zero():
    metrics = regression_metrics([0, 0], [1, 2])
    assert metrics["mape"] is None
    assert metrics["mape_excluded_points"] == 2


def test_inventory_plan_uses_safety_stock_and_forecast_reorder_point():
    plan = inventory_plan(
        forecast_weekly_demand=14,
        demand_error_std=3,
        lead_time_weeks=2,
        z_score=1.65,
    )
    assert plan["safety_stock"] == 1.65 * 3 * np.sqrt(2)
    assert plan["reorder_point"] == 14 * 2 + plan["safety_stock"]


def test_ranking_metrics_are_bounded():
    metrics = ranking_metrics({1, 3}, [3, 2, 1], 2)
    assert metrics["precision_at_k"] == 0.5
    assert metrics["recall_at_k"] == 0.5
    assert metrics["f1_at_k"] == 0.5
    assert 0 < metrics["ndcg_at_k"] < 1


def test_hybrid_recommender_excludes_already_purchased_products():
    products = pd.DataFrame(
        {
            "product_id": [1, 2, 3],
            "category_id": [1, 1, 2],
            "name": ["Oat drink", "Oat beverage", "Dish soap"],
            "brand": ["Farm", "Farm", "Clean"],
            "unit_price": [4.0, 5.0, 3.0],
        }
    )
    purchases = pd.DataFrame({"retailer_id": [10], "product_id": [1], "quantity": [4]})
    model = HybridRecommender().fit(products, purchases)
    results = model.recommend(10, 2)
    assert len(results) == 2
    assert 1 not in {row["product_id"] for row in results}
