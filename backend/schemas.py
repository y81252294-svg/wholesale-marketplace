from datetime import datetime
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.models import OrderStatus, UserRole


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=72)
    role: UserRole
    business_name: str = Field(min_length=2, max_length=200)
    location: str | None = None
    selected_category_ids: list[int] = Field(default_factory=list)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Enter a valid email address.")
        return normalized

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes for bcrypt.")
        return value


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProductCreate(BaseModel):
    category_id: int
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=240)
    brand: str | None = None
    unit_price: float = Field(gt=0)
    min_order_qty: int = Field(default=1, ge=1)
    order_multiple: int = Field(default=1, ge=1)
    description: str | None = None
    stock_qty: int = Field(default=0, ge=0)
    reorder_level: int = Field(default=0, ge=0)
    warehouse_code: str = "default"


class ProductUpdate(BaseModel):
    category_id: int | None = None
    name: str | None = None
    brand: str | None = None
    unit_price: float | None = Field(default=None, gt=0)
    min_order_qty: int | None = Field(default=None, ge=1)
    order_multiple: int | None = Field(default=None, ge=1)
    description: str | None = None
    is_active: bool | None = None


class InventoryUpdate(BaseModel):
    warehouse_code: str = Field(default="default", min_length=1, max_length=80)
    stock_qty: int = Field(ge=0)
    reorder_level: int = Field(ge=0)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    wholesaler_id: int
    category_id: int
    sku: str
    name: str
    brand: str | None
    unit_price: float
    min_order_qty: int
    order_multiple: int
    is_active: bool
    stock_qty: int = 0


class OrderLineInput(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class OrderCreate(BaseModel):
    items: list[OrderLineInput] = Field(min_length=1)


class OrderItemOut(BaseModel):
    product_id: int
    quantity: int
    unit_price: float


class OrderOut(BaseModel):
    id: int
    retailer_id: int
    order_date: datetime
    status: OrderStatus
    total_amount: float
    items: list[OrderItemOut]


class NotificationOut(BaseModel):
    id: int
    kind: str
    message: str
    is_read: bool
    created_at: datetime
