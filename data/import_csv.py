"""Validate a real order-line CSV against the shared marketplace schema."""

import argparse
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from backend.database import Base, SessionLocal, engine
from backend.models import Order, OrderItem, OrderStatus, Product, Retailer

REQUIRED = {"product_id", "retailer_id", "date", "quantity", "unit_price"}


def validate_order_lines(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"], errors="raise", utc=True)
    for column in ("product_id", "retailer_id", "quantity"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype(int)
    frame["unit_price"] = pd.to_numeric(frame["unit_price"], errors="raise")
    if (frame["quantity"] <= 0).any() or (frame["unit_price"] < 0).any():
        raise ValueError("quantity must be positive and unit_price must be non-negative")
    return frame


def import_order_lines(frame: pd.DataFrame):
    """Import validated lines as fulfilled orders linked to existing marketplace IDs."""
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()
    try:
        retailer_ids = set(frame["retailer_id"].astype(int))
        product_ids = set(frame["product_id"].astype(int))
        retailers = {
            row.id: row
            for row in db.query(Retailer).filter(Retailer.id.in_(retailer_ids)).all()
        }
        products = {
            row.id: row
            for row in db.query(Product).filter(Product.id.in_(product_ids)).all()
        }
        if retailer_ids - set(retailers):
            raise ValueError(f"Unknown retailer IDs: {sorted(retailer_ids - set(retailers))}")
        if product_ids - set(products):
            raise ValueError(f"Unknown product IDs: {sorted(product_ids - set(products))}")
        lines = frame.copy()
        lines["retailer_id"] = lines["retailer_id"].astype(int)
        lines["product_id"] = lines["product_id"].astype(int)
        lines["wholesaler_id"] = lines["product_id"].map(
            {product_id: product.wholesaler_id for product_id, product in products.items()}
        )
        if "order_id" in lines.columns:
            group_columns = ["retailer_id", "wholesaler_id", "order_id"]
        else:
            group_columns = ["retailer_id", "wholesaler_id", "date"]
        imported = 0
        for key, group in lines.groupby(group_columns, sort=False):
            retailer_id = int(key[0])
            wholesaler_id = int(key[1])
            order_date = pd.Timestamp(group["date"].min()).to_pydatetime()
            order = Order(
                retailer_id=retailers[retailer_id].id,
                order_date=order_date,
                status=OrderStatus.fulfilled,
                total_amount=0.0,
            )
            db.add(order)
            db.flush()
            for product_id, item_lines in group.groupby("product_id"):
                product_id = int(product_id)
                if products[product_id].wholesaler_id != wholesaler_id:
                    raise ValueError("An imported order contains lines from multiple wholesalers.")
                quantity = int(item_lines["quantity"].sum())
                price = float(
                    (item_lines["quantity"] * item_lines["unit_price"]).sum() / quantity
                )
                db.add(
                    OrderItem(
                        order_id=order.id,
                        product_id=product_id,
                        quantity=quantity,
                        unit_price=price,
                    )
                )
                order.total_amount += quantity * price
            imported += 1
        db.commit()
        return imported
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--persist", action="store_true", help="Insert validated rows as fulfilled orders.")
    args = parser.parse_args()
    normalized = validate_order_lines(args.csv_path)
    target = args.output or args.csv_path.with_name(f"{args.csv_path.stem}.validated.csv")
    normalized.to_csv(target, index=False)
    print(f"Validated {len(normalized)} rows to {target}")
    if args.persist:
        count = import_order_lines(normalized)
        print(f"Imported {count} fulfilled orders using existing marketplace retailer/product IDs.")
