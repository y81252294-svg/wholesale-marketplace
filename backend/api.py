from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload
import pandas as pd

from backend.database import get_db
from backend.models import (
    Category,
    DemandForecast,
    Inventory,
    Notification,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    Recommendation,
    Retailer,
    RetailerCategory,
    User,
    UserRole,
    Wholesaler,
)
from backend.schemas import (
    InventoryUpdate,
    LoginRequest,
    NotificationOut,
    OrderCreate,
    OrderOut,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    RegisterRequest,
    TokenResponse,
)
from backend.security import create_access_token, hash_password, require_roles, verify_password

router = APIRouter()


def retailer_for_user(db: Session, user: User) -> Retailer:
    retailer = db.query(Retailer).filter_by(user_id=user.id).first()
    if not retailer:
        raise HTTPException(status_code=404, detail="Retailer profile not found")
    return retailer


def wholesaler_for_user(db: Session, user: User) -> Wholesaler:
    wholesaler = db.query(Wholesaler).filter_by(user_id=user.id).first()
    if not wholesaler:
        raise HTTPException(status_code=404, detail="Wholesaler profile not found")
    return wholesaler


def to_product_out(db: Session, product: Product) -> ProductOut:
    available = db.query(func.coalesce(func.sum(Inventory.stock_qty), 0)).filter_by(product_id=product.id).scalar()
    return ProductOut.model_validate({**product.__dict__, "stock_qty": int(available or 0)})


def to_order_out(order: Order) -> OrderOut:
    return OrderOut(
        id=order.id,
        retailer_id=order.retailer_id,
        order_date=order.order_date,
        status=order.status,
        total_amount=order.total_amount,
        items=[
            {"product_id": item.product_id, "quantity": item.quantity, "unit_price": item.unit_price}
            for item in order.items
        ],
    )


@router.post("/auth/register", status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    if payload.role == UserRole.admin:
        raise HTTPException(status_code=400, detail="Admin accounts must be provisioned by an operator")
    if db.query(User).filter(func.lower(User.email) == payload.email.lower()).first():
        raise HTTPException(status_code=409, detail="Email is already registered")
    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_approved=False,
    )
    db.add(user)
    db.flush()
    if payload.role == UserRole.retailer:
        profile = Retailer(user_id=user.id, business_name=payload.business_name, location=payload.location)
        db.add(profile)
        db.flush()
        valid_ids = {
            row[0]
            for row in db.query(Category.id).filter(Category.id.in_(payload.selected_category_ids)).all()
        }
        if valid_ids != set(payload.selected_category_ids):
            raise HTTPException(status_code=422, detail="One or more selected categories do not exist")
        if valid_ids:
            db.add_all(
                RetailerCategory(retailer_id=profile.id, category_id=category_id)
                for category_id in valid_ids
            )
    else:
        db.add(Wholesaler(user_id=user.id, business_name=payload.business_name, location=payload.location))
    db.commit()
    return {"id": user.id, "email": user.email, "role": user.role, "is_approved": user.is_approved}


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.email) == payload.email.lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_approved:
        raise HTTPException(status_code=403, detail="Account is pending administrator approval")
    return TokenResponse(access_token=create_access_token(user))


@router.get("/auth/me")
def current_account(user: User = Depends(require_roles(UserRole.retailer, UserRole.wholesaler, UserRole.admin))):
    return {"id": user.id, "email": user.email, "role": user.role.value}


@router.get("/admin/accounts")
def pending_accounts(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.admin)),
):
    return [
        {"id": user.id, "email": user.email, "role": user.role, "created_at": user.created_at}
        for user in db.query(User).filter_by(is_approved=False).order_by(User.created_at).all()
    ]


@router.post("/admin/accounts/{user_id}/approve")
def approve_account(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.admin)),
):
    user = db.get(User, user_id)
    if not user or user.role == UserRole.admin:
        raise HTTPException(status_code=404, detail="Pending account not found")
    user.is_approved = True
    db.commit()
    return {"id": user.id, "is_approved": user.is_approved}


@router.get("/categories")
def list_categories(db: Session = Depends(get_db)):
    return [
        {"id": item.id, "name": item.name, "parent_category_id": item.parent_category_id}
        for item in db.query(Category).order_by(Category.name).all()
    ]


@router.get("/wholesalers")
def list_wholesalers(db: Session = Depends(get_db)):
    return [
        {"id": row.id, "business_name": row.business_name}
        for row in db.query(Wholesaler).order_by(Wholesaler.business_name).all()
    ]


@router.get("/products", response_model=list[ProductOut])
def search_products(
    q: str | None = None,
    category_id: int | None = None,
    supplier_id: int | None = None,
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    available: bool | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Product).filter(Product.is_active.is_(True))
    if q:
        pattern = f"%{q.strip()}%"
        query = query.filter(or_(Product.name.ilike(pattern), Product.sku.ilike(pattern), Product.brand.ilike(pattern)))
    if category_id is not None:
        query = query.filter(Product.category_id == category_id)
    if supplier_id is not None:
        query = query.filter(Product.wholesaler_id == supplier_id)
    if min_price is not None:
        query = query.filter(Product.unit_price >= min_price)
    if max_price is not None:
        query = query.filter(Product.unit_price <= max_price)
    products = query.order_by(Product.name).all()
    outputs = [to_product_out(db, product) for product in products]
    return [item for item in outputs if item.stock_qty > 0] if available else outputs


@router.get("/wholesaler/products", response_model=list[ProductOut])
def wholesaler_products(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.wholesaler)),
):
    wholesaler = wholesaler_for_user(db, user)
    products = (
        db.query(Product)
        .filter(Product.wholesaler_id == wholesaler.id)
        .order_by(Product.name)
        .all()
    )
    return [to_product_out(db, product) for product in products]


@router.post("/products", response_model=ProductOut, status_code=201)
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.wholesaler)),
):
    wholesaler = wholesaler_for_user(db, user)
    if not db.get(Category, payload.category_id):
        raise HTTPException(status_code=404, detail="Category not found")
    if db.query(Product).filter_by(sku=payload.sku).first():
        raise HTTPException(status_code=409, detail="SKU already exists")
    product = Product(
        wholesaler_id=wholesaler.id,
        category_id=payload.category_id,
        sku=payload.sku,
        name=payload.name,
        brand=payload.brand,
        unit_price=payload.unit_price,
        min_order_qty=payload.min_order_qty,
        order_multiple=payload.order_multiple,
        description=payload.description,
    )
    db.add(product)
    db.flush()
    db.add(
        Inventory(
            product_id=product.id,
            warehouse_code=payload.warehouse_code,
            stock_qty=payload.stock_qty,
            reorder_level=payload.reorder_level,
        )
    )
    db.commit()
    db.refresh(product)
    return to_product_out(db, product)


@router.patch("/products/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.wholesaler)),
):
    product = db.get(Product, product_id)
    wholesaler = wholesaler_for_user(db, user)
    if not product or product.wholesaler_id != wholesaler.id:
        raise HTTPException(status_code=404, detail="Product not found")
    updates = payload.model_dump(exclude_unset=True)
    for required_field in ("category_id", "name", "unit_price", "min_order_qty", "order_multiple", "is_active"):
        if required_field in updates and updates[required_field] is None:
            raise HTTPException(status_code=422, detail=f"{required_field} cannot be null")
    if "category_id" in updates and not db.get(Category, updates["category_id"]):
        raise HTTPException(status_code=404, detail="Category not found")
    for key, value in updates.items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return to_product_out(db, product)


@router.put("/products/{product_id}/inventory")
def update_inventory(
    product_id: int,
    payload: InventoryUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.wholesaler)),
):
    wholesaler = wholesaler_for_user(db, user)
    product = db.get(Product, product_id)
    if not product or product.wholesaler_id != wholesaler.id:
        raise HTTPException(status_code=404, detail="Product not found")
    inventory = db.query(Inventory).filter_by(
        product_id=product_id, warehouse_code=payload.warehouse_code
    ).first()
    if inventory is None:
        inventory = Inventory(product_id=product_id, warehouse_code=payload.warehouse_code)
        db.add(inventory)
    inventory.stock_qty = payload.stock_qty
    inventory.reorder_level = payload.reorder_level
    inventory.updated_at = datetime.now(timezone.utc)
    db.commit()
    if inventory.stock_qty <= inventory.reorder_level:
        db.add(
            Notification(
                user_id=user.id,
                kind="low_stock",
                message=f"{product.name} is at or below its reorder level.",
            )
        )
        db.commit()
    return {"product_id": product_id, "stock_qty": inventory.stock_qty, "reorder_level": inventory.reorder_level}


@router.post("/orders", response_model=OrderOut, status_code=201)
def place_order(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.retailer)),
):
    retailer = retailer_for_user(db, user)
    product_ids = [line.product_id for line in payload.items]
    if len(set(product_ids)) != len(product_ids):
        raise HTTPException(status_code=422, detail="Each product may appear only once per order")
    products = db.query(Product).filter(Product.id.in_(product_ids), Product.is_active.is_(True)).all()
    if len(products) != len(product_ids):
        raise HTTPException(status_code=404, detail="One or more products are unavailable")
    if len({product.wholesaler_id for product in products}) != 1:
        raise HTTPException(status_code=422, detail="An order may contain products from one wholesaler only")
    by_id = {product.id: product for product in products}
    total = 0.0
    for line in payload.items:
        product = by_id[line.product_id]
        if line.quantity < product.min_order_qty or line.quantity % product.order_multiple:
            raise HTTPException(
                status_code=422,
                detail=f"{product.name} quantity must be at least {product.min_order_qty} and a multiple of {product.order_multiple}",
            )
        stock = db.query(func.coalesce(func.sum(Inventory.stock_qty), 0)).filter_by(product_id=product.id).scalar()
        if line.quantity > int(stock or 0):
            raise HTTPException(status_code=409, detail=f"Insufficient stock for {product.name}")
        total += line.quantity * product.unit_price
    order = Order(retailer_id=retailer.id, status=OrderStatus.pending, total_amount=total)
    db.add(order)
    db.flush()
    db.add_all(
        OrderItem(
            order_id=order.id,
            product_id=line.product_id,
            quantity=line.quantity,
            unit_price=by_id[line.product_id].unit_price,
        )
        for line in payload.items
    )
    db.commit()
    db.refresh(order)
    return to_order_out(order)


@router.get("/orders", response_model=list[OrderOut])
def list_orders(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.retailer, UserRole.wholesaler, UserRole.admin)),
):
    query = db.query(Order).options(joinedload(Order.items).joinedload(OrderItem.product))
    if user.role == UserRole.retailer:
        query = query.filter(Order.retailer_id == retailer_for_user(db, user).id)
    elif user.role == UserRole.wholesaler:
        wholesaler_id = wholesaler_for_user(db, user).id
        query = query.join(OrderItem).join(Product).filter(Product.wholesaler_id == wholesaler_id).distinct()
    return [to_order_out(order) for order in query.order_by(Order.order_date.desc()).all()]


@router.post("/orders/{order_id}/{decision}", response_model=OrderOut)
def decide_order(
    order_id: int,
    decision: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.wholesaler)),
):
    if decision not in {"confirm", "reject"}:
        raise HTTPException(status_code=422, detail="Decision must be confirm or reject")
    order = (
        db.query(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.product))
        .filter(Order.id == order_id)
        .first()
    )
    wholesaler = wholesaler_for_user(db, user)
    if not order or any(item.product.wholesaler_id != wholesaler.id for item in order.items):
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != OrderStatus.pending:
        raise HTTPException(status_code=409, detail="Only pending orders can be decided")
    if decision == "confirm":
        for item in order.items:
            rows = (
                db.query(Inventory)
                .filter_by(product_id=item.product_id)
                .order_by(Inventory.stock_qty.desc())
                .with_for_update()
                .all()
            )
            if sum(row.stock_qty for row in rows) < item.quantity:
                raise HTTPException(status_code=409, detail=f"Insufficient stock for {item.product.name}")
            remaining = item.quantity
            for row in rows:
                taken = min(row.stock_qty, remaining)
                row.stock_qty -= taken
                remaining -= taken
                if row.stock_qty <= row.reorder_level:
                    db.add(Notification(user_id=user.id, kind="low_stock", message=f"{item.product.name} is low in stock."))
                if remaining == 0:
                    break
        order.status = OrderStatus.confirmed
    else:
        order.status = OrderStatus.rejected
    retailer_user_id = db.query(Retailer.user_id).filter_by(id=order.retailer_id).scalar()
    db.add(
        Notification(
            user_id=retailer_user_id,
            kind="order_status",
            message=f"Order {order.id} was {order.status.value}.",
        )
    )
    db.commit()
    db.refresh(order)
    return to_order_out(order)


@router.get("/notifications", response_model=list[NotificationOut])
def notifications(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.retailer, UserRole.wholesaler, UserRole.admin)),
):
    return (
        db.query(Notification)
        .filter_by(user_id=user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )


@router.get("/analytics/summary")
def analytics_summary(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.retailer, UserRole.wholesaler, UserRole.admin)),
):
    query = db.query(OrderItem).join(Order).filter(Order.status.in_([OrderStatus.confirmed, OrderStatus.fulfilled]))
    if user.role == UserRole.wholesaler:
        query = query.join(Product).filter(Product.wholesaler_id == wholesaler_for_user(db, user).id)
    top_products = (
        query.with_entities(
            OrderItem.product_id,
            func.sum(OrderItem.quantity).label("units"),
            func.sum(OrderItem.quantity * OrderItem.unit_price).label("revenue"),
        )
        .group_by(OrderItem.product_id)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(10)
        .all()
    )
    products = {p.id: p.name for p in db.query(Product).filter(Product.id.in_([r.product_id for r in top_products])).all()}
    low_stock = (
        db.query(Inventory, Product.name)
        .join(Product)
        .filter(Inventory.stock_qty <= Inventory.reorder_level)
        .limit(50)
        .all()
    )
    return {
        "top_products": [
            {"product_id": row.product_id, "name": products[row.product_id], "units_sold": int(row.units), "revenue": float(row.revenue)}
            for row in top_products
        ],
        "low_stock": [
            {"product_id": row.product_id, "name": name, "warehouse_code": row.warehouse_code, "stock_qty": row.stock_qty, "reorder_level": row.reorder_level}
            for row, name in low_stock
        ],
    }


@router.get("/recommendations/{retailer_id}")
def recommendations(
    retailer_id: int,
    k: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.retailer, UserRole.admin)),
):
    retailer = db.get(Retailer, retailer_id)
    if not retailer or (user.role == UserRole.retailer and retailer.user_id != user.id):
        raise HTTPException(status_code=404, detail="Retailer not found")
    stored = (
        db.query(Recommendation, Product)
        .join(Product, Product.id == Recommendation.product_id)
        .filter(Recommendation.retailer_id == retailer_id, Product.is_active.is_(True))
        .order_by(Recommendation.generated_at.desc(), Recommendation.score.desc())
        .limit(k)
        .all()
    )
    if stored:
        return [
            {"product_id": product.id, "name": product.name, "score": rec.score, "model_version": rec.model_version}
            for rec, product in stored
        ]
    active_products = db.query(Product).filter(Product.is_active.is_(True)).all()
    if not active_products:
        return []
    purchase_rows = (
        db.query(Order.retailer_id, OrderItem.product_id, func.sum(OrderItem.quantity).label("quantity"))
        .join(OrderItem, OrderItem.order_id == Order.id)
        .filter(Order.status.in_([OrderStatus.confirmed, OrderStatus.fulfilled]))
        .group_by(Order.retailer_id, OrderItem.product_id)
        .all()
    )
    products_frame = pd.DataFrame(
        [
            {
                "product_id": product.id,
                "category_id": product.category_id,
                "name": product.name,
                "brand": product.brand,
                "unit_price": product.unit_price,
            }
            for product in active_products
        ]
    )
    purchases_frame = pd.DataFrame(
        [
            {"retailer_id": row.retailer_id, "product_id": row.product_id, "quantity": row.quantity}
            for row in purchase_rows
        ],
        columns=["retailer_id", "product_id", "quantity"],
    )
    alpha = float(os.getenv("RECOMMENDATION_ALPHA", "0.5"))
    result_file = Path(__file__).resolve().parents[1] / "results" / "recommendation_metrics.json"
    if "RECOMMENDATION_ALPHA" not in os.environ and result_file.exists():
        with result_file.open(encoding="utf-8") as file:
            alpha = float(json.load(file).get("selected_alpha", alpha))
    from ml.recommendation import HybridRecommender

    model = HybridRecommender(alpha=alpha).fit(
        products_frame,
        purchases_frame,
        {retailer_id: [category.id for category in retailer.selected_categories]},
    )
    results = model.recommend(retailer_id, k)
    db.query(Recommendation).filter_by(retailer_id=retailer_id).delete(synchronize_session=False)
    db.add_all(
        Recommendation(
            retailer_id=retailer_id,
            product_id=row["product_id"],
            score=row["score"],
            model_version=row["model_version"],
        )
        for row in results
    )
    db.commit()
    names = {product.id: product.name for product in active_products}
    return [
        {**row, "name": names[row["product_id"]]}
        for row in results
    ]


@router.get("/forecast/{product_id}")
def forecast(
    product_id: int,
    horizon: int = Query(default=4, ge=1, le=52),
    db: Session = Depends(get_db),
):
    if not db.get(Product, product_id):
        raise HTTPException(status_code=404, detail="Product not found")
    rows = (
        db.query(DemandForecast)
        .filter_by(product_id=product_id)
        .order_by(DemandForecast.generated_at.desc(), DemandForecast.target_period)
        .limit(horizon)
        .all()
    )
    if rows:
        return [
            {
                "product_id": row.product_id,
                "target_period": row.target_period,
                "predicted_qty": row.predicted_qty,
                "model_version": row.model_version,
            }
            for row in rows
        ]
    history = (
        db.query(func.date(Order.order_date).label("order_day"), func.sum(OrderItem.quantity).label("quantity"))
        .join(OrderItem, OrderItem.order_id == Order.id)
        .filter(OrderItem.product_id == product_id, Order.status.in_([OrderStatus.confirmed, OrderStatus.fulfilled]))
        .group_by(func.date(Order.order_date))
        .all()
    )
    weekly: dict[datetime, int] = {}
    for order_day, quantity in history:
        day = datetime.fromisoformat(str(order_day)).date()
        monday = day - timedelta(days=day.weekday())
        week = datetime.combine(monday, datetime.min.time(), tzinfo=timezone.utc)
        weekly[week] = weekly.get(week, 0) + int(quantity)
    if not weekly:
        return []
    ordered_weeks = sorted(weekly)
    latest = ordered_weeks[-1]
    first = ordered_weeks[0]
    week_count = (latest.date() - first.date()).days // 7 + 1
    values = [
        weekly.get(first + timedelta(weeks=index), 0)
        for index in range(week_count)
    ]
    forecast_rows = []
    for step in range(1, horizon + 1):
        target_period = latest + timedelta(weeks=step)
        if len(values) >= 52:
            prediction = float(values[-52 + ((step - 1) % 52)])
            version = "seasonal-naive-live"
        else:
            prediction = float(sum(values[-4:]) / min(4, len(values)))
            version = "moving-average-4-live"
        forecast_rows.append(
            {
                "product_id": product_id,
                "target_period": target_period,
                "predicted_qty": max(0.0, prediction),
                "model_version": version,
            }
        )
    return forecast_rows
