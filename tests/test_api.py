import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend.main import app
from backend.models import Category, Inventory, Product, Retailer, User, UserRole, Wholesaler
from backend.security import hash_password


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    db = testing_session()
    password_hash = hash_password("TestPassword123!")
    category = Category(name="Test Pantry")
    db.add(category)
    admin = User(email="admin@example.test", password_hash=password_hash, role=UserRole.admin, is_approved=True)
    retailer_user = User(
        email="retailer@example.test",
        password_hash=password_hash,
        role=UserRole.retailer,
        is_approved=False,
    )
    wholesaler_user = User(
        email="wholesaler@example.test",
        password_hash=password_hash,
        role=UserRole.wholesaler,
        is_approved=True,
    )
    db.add_all([admin, retailer_user, wholesaler_user])
    db.flush()
    retailer = Retailer(user_id=retailer_user.id, business_name="Test Retail")
    wholesaler = Wholesaler(user_id=wholesaler_user.id, business_name="Test Supply")
    db.add_all([retailer, wholesaler])
    db.flush()
    product = Product(
        wholesaler_id=wholesaler.id,
        category_id=category.id,
        sku="TEST-001",
        name="Test Product",
        unit_price=2.5,
        min_order_qty=2,
        order_multiple=2,
    )
    db.add(product)
    db.flush()
    db.add(Inventory(product_id=product.id, warehouse_code="default", stock_qty=20, reorder_level=2))
    db.commit()
    db.close()

    def override_get_db():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def sign_in(client: TestClient, email: str):
    response = client.post("/auth/login", json={"email": email, "password": "TestPassword123!"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_health_and_admin_approval_flow(client):
    assert client.get("/health").json() == {"status": "ok"}
    registered = client.post(
        "/auth/register",
        json={
            "email": "new-retailer@example.test",
            "password": "NewPassword123!",
            "role": "retailer",
            "business_name": "New Shop",
            "selected_category_ids": [1],
        },
    )
    assert registered.status_code == 201
    pending_login = client.post(
        "/auth/login",
        json={"email": "new-retailer@example.test", "password": "NewPassword123!"},
    )
    assert pending_login.status_code == 403
    response = client.post("/auth/login", json={"email": "retailer@example.test", "password": "TestPassword123!"})
    assert response.status_code == 403
    admin_headers = sign_in(client, "admin@example.test")
    pending = client.get("/admin/accounts", headers=admin_headers)
    assert pending.status_code == 200
    retailer_id = next(row["id"] for row in pending.json() if row["email"] == "retailer@example.test")
    assert client.post(f"/admin/accounts/{retailer_id}/approve", headers=admin_headers).status_code == 200
    assert sign_in(client, "retailer@example.test")["Authorization"].startswith("Bearer ")


def test_order_enforces_pack_multiple_and_wholesaler_can_confirm(client):
    admin_headers = sign_in(client, "admin@example.test")
    pending = client.get("/admin/accounts", headers=admin_headers).json()
    retailer_id = next(row["id"] for row in pending if row["email"] == "retailer@example.test")
    client.post(f"/admin/accounts/{retailer_id}/approve", headers=admin_headers)
    retailer_headers = sign_in(client, "retailer@example.test")
    products = client.get("/products", headers=retailer_headers).json()
    product_id = products[0]["id"]
    invalid = client.post("/orders", headers=retailer_headers, json={"items": [{"product_id": product_id, "quantity": 3}]})
    assert invalid.status_code == 422
    created = client.post("/orders", headers=retailer_headers, json={"items": [{"product_id": product_id, "quantity": 4}]})
    assert created.status_code == 201
    assert created.json()["total_amount"] == 10.0
    wholesaler_headers = sign_in(client, "wholesaler@example.test")
    decision = client.post(f"/orders/{created.json()['id']}/confirm", headers=wholesaler_headers)
    assert decision.status_code == 200
    assert decision.json()["status"] == "confirmed"
    current_stock = client.get("/products").json()[0]["stock_qty"]
    assert current_stock == 16
    forecast = client.get(f"/forecast/{product_id}?horizon=2")
    assert forecast.status_code == 200
    assert len(forecast.json()) == 2
    recommendations = client.get("/recommendations/1", headers=retailer_headers)
    assert recommendations.status_code == 200
