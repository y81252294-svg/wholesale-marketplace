import math


def inventory_plan(forecast_weekly_demand: float, demand_error_std: float, lead_time_weeks: float, z_score: float):
    if min(forecast_weekly_demand, demand_error_std, lead_time_weeks, z_score) < 0:
        raise ValueError("Inventory planning inputs cannot be negative.")
    safety_stock = z_score * demand_error_std * math.sqrt(lead_time_weeks)
    reorder_point = forecast_weekly_demand * lead_time_weeks + safety_stock
    return {"safety_stock": safety_stock, "reorder_point": reorder_point}
