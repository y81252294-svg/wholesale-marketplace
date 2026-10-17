from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.preprocessing import StandardScaler

from ml.metrics import regression_metrics
from ml.preprocessing import add_demand_features, aggregate_weekly_demand, chronological_split


def baseline_predictions(train: pd.DataFrame, target: pd.DataFrame):
    history = train.groupby("product_id")["quantity"]
    means = history.mean()
    latest = train.sort_values("week").groupby("product_id")["quantity"].last()
    mean_prediction = target["product_id"].map(means).fillna(0).to_numpy(dtype=float)
    seasonal = target["lag_52"].copy()
    last_values = target["product_id"].map(latest)
    seasonal = seasonal.fillna(last_values).fillna(0).to_numpy(dtype=float)
    moving_average = []
    for product_id in target["product_id"]:
        recent = train.loc[train["product_id"] == product_id].sort_values("week")["quantity"].tail(4)
        moving_average.append(float(recent.mean()) if not recent.empty else 0.0)
    return {
        "seasonal_naive": np.asarray(seasonal),
        "moving_average_4": np.asarray(moving_average),
        "global_product_mean": mean_prediction,
    }


def build_estimators():
    estimators = {
        "ExtraTrees": ExtraTreesRegressor(
            n_estimators=160, min_samples_leaf=2, random_state=42, n_jobs=-1
        ),
        "RandomForest": RandomForestRegressor(
            n_estimators=160, min_samples_leaf=2, random_state=42, n_jobs=-1
        ),
    }
    try:
        from xgboost import XGBRegressor

        estimators["XGBoost"] = XGBRegressor(
            n_estimators=180,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.9,
            colsample_bytree=0.9,
            objective="reg:squarederror",
            random_state=42,
            n_jobs=-1,
        )
    except ImportError:
        warnings.warn("xgboost is not installed; XGBoost will be omitted.", stacklevel=2)
    return estimators


def _feature_columns(frame):
    excluded = {"week", "quantity", "target"}
    return [column for column in frame.columns if column not in excluded]


def evaluate_demand(order_lines: pd.DataFrame, products: pd.DataFrame):
    weekly = aggregate_weekly_demand(order_lines, products)
    features = add_demand_features(weekly)
    train, validation, test = chronological_split(features)
    # A lag of 52 weeks is unavailable for the earliest observations.
    train_model = train.dropna(subset=["lag_52"]).copy()
    validation_model = validation.dropna(subset=["lag_52"]).copy()
    test_model = test.dropna(subset=["lag_52"]).copy()
    if train_model.empty or validation_model.empty or test_model.empty:
        raise ValueError("Not enough history after lag creation for train/validation/test evaluation.")
    columns = _feature_columns(features)
    x_train = train_model[columns].fillna(0)
    y_train = train_model["target"].to_numpy(dtype=float)
    x_validation = validation_model[columns].fillna(0)
    y_validation = validation_model["target"].to_numpy(dtype=float)
    x_test = test_model[columns].fillna(0)
    y_test = test_model["target"].to_numpy(dtype=float)
    metric_rows = []
    fitted_models = {}
    for name, estimator in build_estimators().items():
        estimator.fit(x_train, y_train)
        validation_pred = np.maximum(0, estimator.predict(x_validation))
        test_pred = np.maximum(0, estimator.predict(x_test))
        validation_metrics = regression_metrics(y_validation, validation_pred)
        metrics = regression_metrics(y_test, test_pred)
        metric_rows.append({"model": name, "split": "test", **metrics})
        fitted_models[name] = {
            "estimator": estimator,
            "validation_mae": validation_metrics["mae"],
            "prediction": test_pred,
        }
    for name, predictions in baseline_predictions(train, test_model).items():
        metric_rows.append(
            {"model": name, "split": "test", **regression_metrics(y_test, np.maximum(0, predictions))}
        )
    lstm = _evaluate_lstm(train_model, validation_model, test_model, columns, y_train, y_validation, y_test)
    if lstm is None:
        metric_rows.append(
            {
                "model": "LSTM",
                "split": "test",
                "status": "skipped_insufficient_history",
                "mae": None,
                "rmse": None,
                "wape": None,
                "mape": None,
                "mape_excluded_points": 0,
                "n_points": len(test_model),
            }
        )
    else:
        metric_rows.append({"model": "LSTM", "split": "test", **lstm})
    predictions_frame = test_model[["product_id", "week", "target"]].copy()
    for name, model in fitted_models.items():
        predictions_frame[f"pred_{name}"] = model["prediction"]
    return {
        "weekly": weekly,
        "train": train,
        "validation": validation,
        "test": test_model,
        "metrics": pd.DataFrame(metric_rows),
        "predictions": predictions_frame,
        "validation_mae": {name: row["validation_mae"] for name, row in fitted_models.items()},
    }


def _evaluate_lstm(train, validation, test, columns, y_train, y_validation, y_test):
    product_weeks = train.groupby("product_id")["week"].nunique()
    if product_weeks.empty or int(product_weeks.min()) < 80 or len(train) < 1000:
        return None
    try:
        from tensorflow.keras import Sequential
        from tensorflow.keras.layers import Dense, LSTM
    except ImportError:
        return None

    # Fit scaling only on training features/targets; validation and test never influence it.
    x_scaler = StandardScaler().fit(train[columns].fillna(0))
    y_scaler = StandardScaler().fit(y_train.reshape(-1, 1))
    x_train = x_scaler.transform(train[columns].fillna(0))
    x_validation = x_scaler.transform(validation[columns].fillna(0))
    x_test = x_scaler.transform(test[columns].fillna(0))
    train_sequences = _sequence_rows(train, x_train, y_train)
    validation_sequences = _sequence_rows(validation, x_validation, y_validation)
    test_sequences = _sequence_rows(test, x_test, y_test)
    if any(len(values[0]) == 0 for values in (train_sequences, validation_sequences, test_sequences)):
        return None
    x_train_seq, y_train_seq = train_sequences
    x_val_seq, y_val_seq = validation_sequences
    x_test_seq, _ = test_sequences
    model = Sequential(
        [
            LSTM(32, input_shape=(x_train_seq.shape[1], x_train_seq.shape[2])),
            Dense(16, activation="relu"),
            Dense(1),
        ]
    )
    model.compile(optimizer="adam", loss="mae")
    model.fit(
        x_train_seq,
        y_scaler.transform(y_train_seq.reshape(-1, 1)),
        validation_data=(x_val_seq, y_scaler.transform(y_val.reshape(-1, 1))),
        epochs=20,
        batch_size=32,
        verbose=0,
        callbacks=[],
    )
    scaled_prediction = model.predict(x_test_seq, verbose=0).reshape(-1, 1)
    predicted = np.maximum(0, y_scaler.inverse_transform(scaled_prediction).ravel())
    # Test sequence filtering should retain all rows since each test product follows historical data.
    actual = np.asarray([target for _, target in test_sequences[1]])
    return regression_metrics(actual, predicted)


def _sequence_rows(frame, features, targets, sequence_length=8):
    sequences, sequence_targets = [], []
    for _, indices in frame.groupby("product_id", sort=False).groups.items():
        positions = list(frame.index.get_indexer(indices))
        for end in range(sequence_length, len(positions) + 1):
            selected = positions[end - sequence_length : end]
            sequences.append(features[selected])
            sequence_targets.append(targets[selected[-1]])
    return np.asarray(sequences), np.asarray(sequence_targets)
