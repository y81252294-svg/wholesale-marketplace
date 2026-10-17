"""Generate clearly labelled synthetic marketplace data and seed the configured database."""

import argparse
import csv
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from sqlalchemy.orm import Session

from backend.database import Base, SessionLocal, engine
from backend.models import (
    Category,
    Inventory,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    Retailer,
    RetailerCategory,
    User,
    UserRole,
    Wholesaler,
)
from backend.security import hash_password


DATA_DIR = Path(__file__).resolve().parent
RESULT_ROWS = (
    "product_id", "retailer_id", "date", "quantity", "unit_price", "stock_qty", "is_synthetic"
)
PRODUCT_ROWS = (
    "product_id",
    "wholesaler_id",
    "category_id",
    "sku",
    "name",
    "brand",
    "unit_price",
    "min_order_qty",
    "order_multiple",
    "stock_qty",
    "is_synthetic",
)


def generate_synthetic_data(output_dir: Path = DATA_DIR, seed: int = 42):
    rng = np.random.default_rng(seed)
    random.seed(seed)
    end_date = date.today()
    start_date = end_date - timedelta(weeks=104)
    start_date -= timedelta(days=start_date.weekday())
    weeks = [start_date + timedelta(weeks=i) for i in range(104)]
    categories = [
        "Beverages", "Snacks", "Cleaning", "Personal Care", "Pantry", "Dairy",
        "Frozen", "Paper Goods", "Pet Supplies", "Produce", "Baking", "Household",
    ]
    brands = ["Northstar", "Harbor", "Evergreen", "Market House", "Cedar & Co."]
    product_names = [
        "Sparkling Water", "Roasted Nuts", "Dish Soap", "Hand Wash", "Pasta",
        "Oat Milk", "Frozen Berries", "Paper Towels", "Pet Treats", "Olive Oil",
        "Baking Flour", "Storage Bags", "Granola Bars", "Laundry Detergent",
        "Canned Tomatoes", "Green Tea", "Coffee Beans", "Rice Crackers",
    ]
    retailer_choices = []
    products = []
    for product_id in range(1, 301):
        category_idx = (product_id - 1) % len(categories)
        product_name = product_names[(product_id - 1) % len(product_names)]
        products.append(
            {
                "product_id": product_id,
                "wholesaler_id": (product_id - 1) % 5 + 1,
                "category_id": category_idx + 1,
                "sku": f"SYN-{product_id:04d}",
                "name": f"{product_name} {product_id:03d}",
                "brand": brands[(product_id - 1) % len(brands)],
                "unit_price": round(float(rng.uniform(1.25, 38.0)), 2),
                "min_order_qty": 1,
                "order_multiple": int(rng.choice([1, 1, 2, 6, 12])),
                "stock_qty": int(rng.integers(150, 1200)),
                "is_synthetic": True,
            }
        )
    for retailer_id in range(1, 51):
        selected = sorted(random.sample(range(1, len(categories) + 1), k=4))
        retailer_choices.append((retailer_id, selected))

    product_price = {p["product_id"]: p["unit_price"] for p in products}
    inventory = {p["product_id"]: p["stock_qty"] for p in products}
    line_rows = []
    order_rows = []
    order_id = 0
    for retailer_id, preferred_categories in retailer_choices:
        relevant = [
            p["product_id"]
            for p in products
            if p["category_id"] in preferred_categories
        ]
        for week_index, week_start in enumerate(weeks):
            seasonal = 1.0 + 0.35 * np.sin(2 * np.pi * week_index / 52.0)
            holiday_boost = 1.6 if week_start.month in (11, 12) else 1.0
            if rng.random() > min(0.92, 0.62 * seasonal * holiday_boost):
                continue
            selected_products = rng.choice(
                relevant, size=min(len(relevant), int(rng.integers(1, 5))), replace=False
            )
            order_id += 1
            order_rows.append((order_id, retailer_id, week_start))
            for product_id in selected_products:
                if rng.random() < 0.48:  # Deliberately intermittent demand.
                    continue
                qty = max(1, int(rng.poisson(5 * seasonal * holiday_boost)))
                event_date = week_start + timedelta(days=int(rng.integers(0, 7)))
                line_rows.append(
                    {
                        "product_id": int(product_id),
                        "retailer_id": retailer_id,
                        "date": event_date.isoformat(),
                        "quantity": qty,
                        "unit_price": product_price[int(product_id)],
                        "stock_qty": inventory[int(product_id)],
                        "is_synthetic": True,
                    }
                )

    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "products.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=PRODUCT_ROWS)
        writer.writeheader()
        writer.writerows(products)
    with (output_dir / "order_lines.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=RESULT_ROWS)
        writer.writeheader()
        writer.writerows(line_rows)
    with (output_dir / "synthetic_orders.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(("order_id", "retailer_id", "order_date", "is_synthetic"))
        writer.writerows((row[0], row[1], row[2].isoformat(), True) for row in order_rows)
    return categories, products, retailer_choices, line_rows, order_rows


def seed_database(reset: bool = False, seed: int = 42):
    if reset:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()
    try:
        if db.query(User).count():
            raise RuntimeError("Database already contains users. Pass --reset to replace seeded data.")
        categories, products, retailer_choices, lines, order_rows = generate_synthetic_data(seed=seed)
        category_models = [Category(id=i + 1, name=name) for i, name in enumerate(categories)]
        db.add_all(category_models)
        password_hash = hash_password("SyntheticDemo123!")
        wholesaler_models = []
        for index in range(1, 6):
            user = User(
                email=f"synthetic-wholesaler-{index}@example.test",
                password_hash=password_hash,
                role=UserRole.wholesaler,
                is_approved=True,
            )
            db.add(user)
            db.flush()
            profile = Wholesaler(user_id=user.id, business_name=f"Synthetic Supply Co. {index}")
            db.add(profile)
            db.flush()
            wholesaler_models.append(profile)
        retailer_models = {}
        for index in range(1, 51):
            user = User(
                email=f"synthetic-retailer-{index}@example.test",
                password_hash=password_hash,
                role=UserRole.retailer,
                is_approved=True,
            )
            db.add(user)
            db.flush()
            profile = Retailer(user_id=user.id, business_name=f"Synthetic Retail {index}", location="Synthetic Region")
            db.add(profile)
            db.flush()
            retailer_models[index] = profile
            categories_selected = retailer_choices[index - 1][1]
            db.add_all(
                RetailerCategory(retailer_id=profile.id, category_id=category_id)
                for category_id in categories_selected
            )
        admin = User(
            email="synthetic-admin@example.test",
            password_hash=password_hash,
            role=UserRole.admin,
            is_approved=True,
        )
        db.add(admin)
        db.flush()
        product_models = {}
        for row in products:
            product = Product(
                id=row["product_id"],
                wholesaler_id=wholesaler_models[row["wholesaler_id"] - 1].id,
                category_id=row["category_id"],
                sku=row["sku"],
                name=row["name"],
                brand=row["brand"],
                unit_price=row["unit_price"],
                min_order_qty=row["min_order_qty"],
                order_multiple=row["order_multiple"],
            )
            db.add(product)
            db.flush()
            product_models[row["product_id"]] = product
            db.add(
                Inventory(
                    product_id=product.id,
                    warehouse_code="default",
                    stock_qty=row["stock_qty"],
                    reorder_level=max(10, row["stock_qty"] // 10),
                )
            )
        order_by_id = {}
        for order_id, retailer_id, order_date in order_rows:
            order = Order(
                id=order_id,
                retailer_id=retailer_models[retailer_id].id,
                order_date=datetime.combine(order_date, datetime.min.time(), tzinfo=timezone.utc),
                status=OrderStatus.fulfilled,
                total_amount=0.0,
            )
            db.add(order)
            order_by_id[order_id] = order
        # The exported line file includes retailer and event date. Match each line to the
        # generated retailer/week order without requiring the CSV to carry internal IDs.
        grouped_orders = {}
        for order_id, retailer_id, week_start in order_rows:
            grouped_orders[(retailer_id, week_start)] = order_by_id[order_id]
        for line in lines:
            event_date = date.fromisoformat(line["date"])
            week_start = event_date - timedelta(days=event_date.weekday())
            order = grouped_orders.get((line["retailer_id"], week_start))
            if order is None:
                raise RuntimeError(f"Generated line has no order for retailer {line['retailer_id']} week {week_start}")
            product = product_models[line["product_id"]]
            db.add(
                OrderItem(
                    order=order,
                    product_id=product.id,
                    quantity=line["quantity"],
                    unit_price=product.unit_price,
                )
            )
            order.total_amount += line["quantity"] * product.unit_price
        db.commit()
        print(
            f"Synthetic seed complete: 50 retailers, 5 wholesalers, 300 products, "
            f"{len(order_rows)} orders, {len(lines)} order lines. "
            "Demo password for all synthetic users: SyntheticDemo123!"
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Drop and recreate database tables before seeding.")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    seed_database(reset=args.reset, seed=args.seed)
