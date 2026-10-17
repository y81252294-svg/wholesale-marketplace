import numpy as np


def regression_metrics(actual, predicted):
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    if actual.shape != predicted.shape:
        raise ValueError("actual and predicted must have the same shape")
    if actual.size == 0:
        raise ValueError("Metrics require at least one observation")
    errors = predicted - actual
    nonzero = actual > 0
    mape = float(np.mean(np.abs(errors[nonzero] / actual[nonzero])) * 100) if nonzero.any() else None
    denominator = float(np.sum(np.abs(actual)))
    return {
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "wape": float(np.sum(np.abs(errors)) / denominator * 100) if denominator else None,
        "mape": mape,
        "mape_excluded_points": int((~nonzero).sum()),
        "n_points": int(actual.size),
    }
