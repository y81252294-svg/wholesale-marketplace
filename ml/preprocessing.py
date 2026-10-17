from __future__ import annotations

import numpy as np
import pandas as pd


def aggregate_weekly_demand(
    order_lines: pd.DataFrame,
    products: pd.DataFrame | None = None,
    inventory: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Aggregate line items to complete product-week series, retaining zero-demand weeks."""
    required = {"product_id", "date", "quantity"}
    missing = required - set(order_lines.columns)
    if missing:
        raise ValueError(f"order_lines is missing required columns: {sorted(missing)}")
    lines = order_lines.copy()
    if lines.empty:
        raise ValueError("At least one order line is required to establish the weekly date range.")
    lines["date"] = pd.to_datetime(lines["date"], utc=True).dt.tz_convert(None)
    lines["week"] = lines["date"].dt.to_period("W-SUN").dt.start_time
    weekly = lines.groupby(["product_id", "week"], as_index=False)["quantity"].sum()
    if products is not None:
        product_ids = products["product_id"].drop_duplicates().tolist()
    else:
        product_ids = lines["product_id"].drop_duplicates().tolist()
    weeks = pd.date_range(weekly["week"].min(), weekly["week"].max(), freq="W-MON")
    grid = pd.MultiIndex.from_product([product_ids, weeks], names=["product_id", "week"]).to_frame(index=False)
    result = grid.merge(weekly, on=["product_id", "week"], how="left")
    result["quantity"] = result["quantity"].fillna(0).astype(float)
    if products is not None:
        product_features = [column for column in ("product_id", "unit_price", "stock_qty") if column in products]
        result = result.merge(products[product_features].drop_duplicates("product_id"), on="product_id", how="left")
    if inventory is not None and {"product_id", "stock_qty"}.issubset(inventory.columns):
        stock = inventory.groupby("product_id", as_index=False)["stock_qty"].sum()
        if "stock_qty" in result.columns:
            result = result.drop(columns="stock_qty")
        result = result.merge(stock, on="product_id", how="left")
    return result.sort_values(["product_id", "week"]).reset_index(drop=True)


def add_demand_features(
    weekly: pd.DataFrame,
    lags: tuple[int, ...] = (1, 2, 4, 8, 13, 26, 52),
    rolling_windows: tuple[int, ...] = (4, 13, 26),
) -> pd.DataFrame:
    """Create strictly past-looking features to avoid target leakage."""
    required = {"product_id", "week", "quantity"}
    missing = required - set(weekly.columns)
    if missing:
        raise ValueError(f"weekly is missing required columns: {sorted(missing)}")
    frame = weekly.copy().sort_values(["product_id", "week"])
    frame["week"] = pd.to_datetime(frame["week"])
    grouped = frame.groupby("product_id", sort=False)["quantity"]
    for lag in lags:
        frame[f"lag_{lag}"] = grouped.shift(lag)
    shifted = frame.groupby("product_id", sort=False)["quantity"].shift(1)
    for window in rolling_windows:
        rolling = shifted.groupby(frame["product_id"]).rolling(window, min_periods=1)
        frame[f"rolling_mean_{window}"] = rolling.mean().reset_index(level=0, drop=True)
        frame[f"rolling_std_{window}"] = rolling.std().reset_index(level=0, drop=True).fillna(0)
    frame["week_of_year"] = frame["week"].dt.isocalendar().week.astype(int)
    frame["month"] = frame["week"].dt.month
    frame["holiday_flag"] = frame["week"].dt.month.isin([1, 11, 12]).astype(int)
    frame["week_sin"] = np.sin(2 * np.pi * frame["week_of_year"] / 52.0)
    frame["week_cos"] = np.cos(2 * np.pi * frame["week_of_year"] / 52.0)
    positive = frame["quantity"].gt(0)
    run_group = positive.groupby(frame["product_id"]).cumsum()
    frame["zero_run_length"] = (
        (~positive).groupby([frame["product_id"], run_group]).cumsum().astype(int)
    )
    frame["target"] = frame["quantity"]
    return frame


def chronological_split(frame: pd.DataFrame, train_fraction=0.7, validation_fraction=0.15):
    """Split on unique timestamps so no product-week leaks across partitions."""
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("Split fractions must be between zero and one.")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("Train and validation fractions must sum to less than one.")
    times = np.array(sorted(pd.to_datetime(frame["week"]).unique()))
    if len(times) < 3:
        raise ValueError("At least three distinct weeks are required for chronological splitting.")
    train_end = min(max(1, int(len(times) * train_fraction)), len(times) - 2)
    validation_end = max(train_end + 1, int(len(times) * (train_fraction + validation_fraction)))
    validation_end = min(validation_end, len(times) - 1)
    train_weeks = times[:train_end]
    validation_weeks = times[train_end:validation_end]
    test_weeks = times[validation_end:]
    return (
        frame[pd.to_datetime(frame["week"]).isin(train_weeks)].copy(),
        frame[pd.to_datetime(frame["week"]).isin(validation_weeks)].copy(),
        frame[pd.to_datetime(frame["week"]).isin(test_weeks)].copy(),
    )
