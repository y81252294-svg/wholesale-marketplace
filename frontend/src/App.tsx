import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

type Role = "retailer" | "wholesaler" | "admin";
type Account = { id: number; email: string; role: Role };
type Product = {
  id: number;
  wholesaler_id: number;
  category_id: number;
  sku: string;
  name: string;
  brand?: string;
  unit_price: number;
  min_order_qty: number;
  order_multiple: number;
  is_active: boolean;
  stock_qty: number;
};
type Order = {
  id: number;
  status: string;
  total_amount: number;
  order_date: string;
  items: { product_id: number; quantity: number; unit_price: number }[];
};
type Notice = { id: number; kind: string; message: string; is_read: boolean };
type Analytics = {
  top_products: { product_id: number; name: string; units_sold: number; revenue: number }[];
  low_stock: { product_id: number; name: string; warehouse_code: string; stock_qty: number; reorder_level: number }[];
};
type PendingAccount = { id: number; email: string; role: Role; created_at: string };
type ForecastPoint = { product_id: number; target_period: string; predicted_qty: number; model_version: string };

async function request<T>(path: string, token?: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${response.status})`);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem("marketwise-token") ?? "");
  const [account, setAccount] = useState<Account | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [recommendations, setRecommendations] = useState<Product[]>([]);
  const [summary, setSummary] = useState<Analytics>({ top_products: [], low_stock: [] });
  const [pendingAccounts, setPendingAccounts] = useState<PendingAccount[]>([]);
  const [forecast, setForecast] = useState<ForecastPoint[]>([]);
  const [search, setSearch] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [supplierId, setSupplierId] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [categories, setCategories] = useState<{ id: number; name: string }[]>([]);
  const [suppliers, setSuppliers] = useState<{ id: number; business_name: string }[]>([]);
  const [availableOnly, setAvailableOnly] = useState(false);
  const [cart, setCart] = useState<Record<number, number>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [registerMode, setRegisterMode] = useState(false);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const me = await request<Account>("/auth/me", token);
      setAccount(me);
      const [catalog, orderList, noticesList, dashboard] = await Promise.all([
        request<Product[]>(me.role === "wholesaler" ? "/wholesaler/products" : "/products", token),
        request<Order[]>("/orders", token),
        request<Notice[]>("/notifications", token),
        request<Analytics>("/analytics/summary", token),
      ]);
      setProducts(catalog);
      setOrders(orderList);
      setNotices(noticesList);
      setSummary(dashboard);
      if (me.role === "admin") setPendingAccounts(await request<PendingAccount[]>("/admin/accounts", token));
      if (me.role === "retailer") {
        const recs = await request<{ product_id: number }[]>(`/recommendations/${me.id}?k=6`, token);
        setRecommendations(recs.map((entry) => catalog.find((product) => product.id === entry.product_id)).filter(Boolean) as Product[]);
        if (catalog[0]) setForecast(await request<ForecastPoint[]>(`/forecast/${catalog[0].id}?horizon=8`, token));
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load marketplace data");
    }
  }, [token]);

  useEffect(() => {
    Promise.all([
      request<typeof categories>("/categories"),
      request<typeof suppliers>("/wholesalers"),
    ]).then(([categoryRows, supplierRows]) => {
      setCategories(categoryRows);
      setSuppliers(supplierRows);
    }).catch((caught: unknown) => {
      setError(caught instanceof Error ? caught.message : "Could not load catalog filters");
    });
  }, []);

  useEffect(() => {
    if (token) void refresh();
    else setAccount(null);
  }, [token, refresh]);

  const visibleProducts = useMemo(
    () =>
      products.filter((product) => {
        const searchMatch = `${product.name} ${product.sku} ${product.brand ?? ""}`.toLowerCase().includes(search.toLowerCase());
        return (
          searchMatch &&
          (!categoryId || product.category_id === Number(categoryId)) &&
          (!supplierId || product.wholesaler_id === Number(supplierId)) &&
          (!maxPrice || product.unit_price <= Number(maxPrice)) &&
          (!availableOnly || product.stock_qty > 0)
        );
      }),
    [products, search, categoryId, supplierId, maxPrice, availableOnly],
  );

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const payload = {
        email: String(data.get("email")),
        password: String(data.get("password")),
      };
      if (registerMode) {
        await request("/auth/register", undefined, {
          method: "POST",
          body: JSON.stringify({
            ...payload,
            role: String(data.get("role")),
            business_name: String(data.get("business_name")),
            selected_category_ids: data.getAll("selected_category_ids").map(Number),
          }),
        });
        setNotice("Account created. An administrator must approve it before you can sign in.");
      } else {
        const result = await request<{ access_token: string }>("/auth/login", undefined, {
          method: "POST",
          body: JSON.stringify(payload),
        });
        localStorage.setItem("marketwise-token", result.access_token);
        setToken(result.access_token);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  }

  function changeCart(product: Product, direction: 1 | -1) {
    setCart((current) => {
      const nextQuantity = (current[product.id] ?? 0) + direction * product.order_multiple;
      if (direction < 0 && nextQuantity < product.min_order_qty) {
        const { [product.id]: _removed, ...rest } = current;
        return rest;
      }
      const quantity = Math.ceil(Math.max(product.min_order_qty, nextQuantity) / product.order_multiple) * product.order_multiple;
      return { ...current, [product.id]: quantity };
    });
  }

  async function placeOrder() {
    const items = Object.entries(cart).filter(([, quantity]) => quantity > 0).map(([product_id, quantity]) => ({ product_id: Number(product_id), quantity }));
    if (!items.length) return;
    setBusy(true);
    try {
      await request("/orders", token, { method: "POST", body: JSON.stringify({ items }) });
      setCart({});
      setNotice("Order placed and sent to the wholesaler for confirmation.");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not place order");
    } finally {
      setBusy(false);
    }
  }

  async function decideOrder(orderId: number, decision: "confirm" | "reject") {
    try {
      await request(`/orders/${orderId}/${decision}`, token, { method: "POST" });
      setNotice(`Order ${decision}ed.`);
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not update order");
    }
  }

  async function approve(userId: number) {
    try {
      await request(`/admin/accounts/${userId}/approve`, token, { method: "POST" });
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not approve account");
    }
  }

  async function createProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const payload: Record<string, string | number> = Object.fromEntries(data.entries()) as Record<string, string>;
    for (const field of ["category_id", "unit_price", "min_order_qty", "order_multiple", "stock_qty", "reorder_level"]) {
      payload[field] = Number(payload[field]);
    }
    try {
      await request("/products", token, { method: "POST", body: JSON.stringify(payload) });
      form.reset();
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create product");
    }
  }

  async function editProduct(product: Product) {
    const name = window.prompt("Product name", product.name);
    if (!name) return;
    const rawPrice = window.prompt("Unit price", String(product.unit_price));
    const unit_price = Number(rawPrice);
    if (!rawPrice || !Number.isFinite(unit_price) || unit_price <= 0) return;
    try {
      await request(`/products/${product.id}`, token, { method: "PATCH", body: JSON.stringify({ name, unit_price }) });
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not update product");
    }
  }

  async function updateStock(product: Product) {
    const rawStock = window.prompt("New total stock quantity", String(product.stock_qty));
    const stock_qty = Number(rawStock);
    if (rawStock === null || !Number.isInteger(stock_qty) || stock_qty < 0) return;
    try {
      await request(`/products/${product.id}/inventory`, token, {
        method: "PUT",
        body: JSON.stringify({ warehouse_code: "default", stock_qty, reorder_level: Math.max(1, Math.floor(stock_qty * 0.1)) }),
      });
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not update stock");
    }
  }

  if (!account) {
    return (
      <main className="grid min-h-screen place-items-center px-5 py-12">
        <section className="panel w-full max-w-md p-8">
          <Brand />
          <p className="mt-5 text-sm text-slate-500">A smarter way to source, forecast, and replenish.</p>
          {notice && <p className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-leaf">{notice}</p>}
          {error && <p className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}
          <form className="mt-6 grid gap-3" onSubmit={submitAuth}>
            <input className="field" name="email" type="email" placeholder="Work email" required />
            <input className="field" name="password" type="password" placeholder="Password (8+ characters)" required minLength={8} />
            {registerMode && <div className="grid grid-cols-2 gap-3">
              <input className="field" name="business_name" placeholder="Business name" required />
              <select className="field" name="role" defaultValue="retailer">
                <option value="retailer">Retailer</option>
                <option value="wholesaler">Wholesaler</option>
              </select>
              <label className="col-span-2 text-xs text-slate-500">Choose product categories for personalized recommendations
                <select className="field mt-1 w-full" name="selected_category_ids" multiple size={4}>
                  {categories.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label>
            </div>}
            <button className="button-primary mt-2" disabled={busy}>{registerMode ? "Request access" : "Sign in"}</button>
            <button className="text-sm text-leaf" type="button" onClick={() => setRegisterMode(!registerMode)}>
              {registerMode ? "Already approved? Sign in" : "New here? Create an account"}
            </button>
          </form>
          <p className="mt-5 text-xs leading-5 text-slate-500">New accounts require administrator approval. Demo accounts are created by the synthetic seed command.</p>
        </section>
      </main>
    );
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4">
          <Brand />
          <div className="flex items-center gap-4 text-sm">
            <span className="hidden text-slate-500 sm:block">{account.email} <span className="capitalize">· {account.role}</span></span>
            <button
              className="button-muted px-3 py-2"
              onClick={() => {
                localStorage.removeItem("marketwise-token");
                setToken("");
                setCart({});
              }}
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-5 py-8">
        {(error || notice) && (
          <div className={`mb-5 rounded-xl p-3 text-sm ${error ? "bg-red-50 text-red-700" : "bg-emerald-50 text-leaf"}`}>
            {error || notice}
            <button className="float-right px-2" onClick={() => { setError(""); setNotice(""); }}>×</button>
          </div>
        )}
        <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="eyebrow">MARKETPLACE OVERVIEW</p>
            <h1 className="mt-1 text-3xl font-semibold tracking-tight">Good afternoon</h1>
            <p className="mt-1 text-sm text-slate-500">Your wholesale operations, in one place.</p>
          </div>
          <span className="rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-medium text-leaf">Live marketplace</span>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard label="Products available" value={products.length.toLocaleString()} detail="Across verified suppliers" />
          <StatCard label="Open orders" value={orders.filter((order) => order.status === "pending").length.toString()} detail={`${orders.length} total orders`} />
          <StatCard label="Notifications" value={notices.filter((item) => !item.is_read).length.toString()} detail="Stock and order updates" />
        </div>

        {account.role === "admin" && (
          <section className="panel mt-6 p-5">
            <SectionTitle title="Account approvals" subtitle="Review new retailers and wholesalers" />
            {pendingAccounts.length ? pendingAccounts.map((pending) => (
              <div className="flex items-center justify-between border-t border-line py-3" key={pending.id}>
                <div><p className="font-medium">{pending.email}</p><p className="text-xs capitalize text-slate-500">{pending.role}</p></div>
                <button className="button-primary px-3 py-2" onClick={() => void approve(pending.id)}>Approve</button>
              </div>
            )) : <p className="mt-4 text-sm text-slate-500">No accounts waiting for approval.</p>}
          </section>
        )}

        {account.role === "wholesaler" && (
          <section className="panel mt-6 p-5">
            <SectionTitle title="Add a product" subtitle="Publish an item to the retailer catalog" />
            <form className="grid gap-3 md:grid-cols-4" onSubmit={createProduct}>
              <input className="field" name="sku" placeholder="SKU" required />
              <input className="field" name="name" placeholder="Product name" required />
              <input className="field" name="brand" placeholder="Brand" />
              <select className="field" name="category_id" required defaultValue="">
                <option value="" disabled>Category</option>{categories.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
              <input className="field" name="unit_price" type="number" min="0.01" step="0.01" placeholder="Unit price" required />
              <input className="field" name="min_order_qty" type="number" min="1" defaultValue="1" placeholder="Minimum qty" />
              <input className="field" name="order_multiple" type="number" min="1" defaultValue="1" placeholder="Pack multiple" />
              <input className="field" name="stock_qty" type="number" min="0" defaultValue="0" placeholder="Initial stock" />
              <input className="field" name="reorder_level" type="number" min="0" defaultValue="0" placeholder="Reorder level" />
              <input className="field" name="warehouse_code" defaultValue="default" placeholder="Warehouse code" />
              <button className="button-primary md:col-span-2">Publish product</button>
            </form>
          </section>
        )}

        <div className="mt-6 grid gap-6 lg:grid-cols-[1.55fr_1fr]">
          <section className="panel p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <SectionTitle title={account.role === "wholesaler" ? "Your catalog" : "Find products"} subtitle="Search and source from approved wholesalers" />
              {account.role === "retailer" && <span className="rounded-lg bg-canvas px-3 py-2 text-xs text-slate-500">Cart {Object.values(cart).reduce((sum, qty) => sum + qty, 0)} units</span>}
            </div>
            <div className="mb-4 grid gap-2 sm:grid-cols-[1fr_150px_160px_120px]">
              <input className="field" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search product, SKU, brand..." />
              <select className="field" value={categoryId} onChange={(event) => setCategoryId(event.target.value)}>
                <option value="">All categories</option>{categories.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
              <select className="field" value={supplierId} onChange={(event) => setSupplierId(event.target.value)}>
                <option value="">All suppliers</option>{suppliers.map((item) => <option key={item.id} value={item.id}>{item.business_name}</option>)}
              </select>
              <input className="field" type="number" min="0" placeholder="Max price" value={maxPrice} onChange={(event) => setMaxPrice(event.target.value)} />
            </div>
            {account.role === "retailer" && <label className="mb-4 flex items-center gap-2 text-xs text-slate-500"><input type="checkbox" checked={availableOnly} onChange={(event) => setAvailableOnly(event.target.checked)} />In-stock products only</label>}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[600px] text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-slate-400"><tr><th className="pb-3">Product</th><th className="pb-3">Unit price</th><th className="pb-3">Stock</th><th className="pb-3">Order pack</th><th className="pb-3"></th></tr></thead>
                <tbody>
                  {visibleProducts.slice(0, 30).map((product) => (
                    <tr className="border-t border-line" key={product.id}>
                      <td className="py-3"><p className="font-medium">{product.name}</p><p className="text-xs text-slate-400">{product.brand ?? product.sku}</p></td>
                      <td>${product.unit_price.toFixed(2)}</td>
                      <td><span className={product.stock_qty <= 10 ? "text-amber-700" : "text-slate-600"}>{product.stock_qty}</span></td>
                      <td className="text-slate-500">{product.min_order_qty}+ / ×{product.order_multiple}</td>
                      <td className="text-right">
                        {account.role === "retailer" && <div className="flex items-center justify-end gap-2">
                          {cart[product.id] && <><button className="button-muted px-2 py-2" aria-label={`Remove one pack of ${product.name}`} onClick={() => changeCart(product, -1)}>−</button><span className="min-w-6 text-center text-xs">{cart[product.id]}</span></>}
                          <button className="button-muted px-3 py-2" disabled={!product.stock_qty} onClick={() => changeCart(product, 1)}>Add</button>
                        </div>}
                        {account.role === "wholesaler" && <div className="flex justify-end gap-1"><button className="button-muted px-2 py-2" onClick={() => void editProduct(product)}>Edit</button><button className="button-muted px-2 py-2" onClick={() => void updateStock(product)}>Stock</button></div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!visibleProducts.length && <p className="py-8 text-center text-sm text-slate-500">No products match these filters.</p>}
            </div>
            {account.role === "retailer" && <div className="mt-4 flex justify-end"><button className="button-primary" disabled={busy || !Object.keys(cart).length} onClick={() => void placeOrder()}>Place order</button></div>}
          </section>

          <section className="panel p-5">
            <SectionTitle title="Demand outlook" subtitle="Forecast for the next replenishment periods" />
            <div className="mt-4 h-60">
              {forecast.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={forecast}>
                    <defs><linearGradient id="demand" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#236b4b" stopOpacity={0.2} /><stop offset="95%" stopColor="#236b4b" stopOpacity={0} /></linearGradient></defs>
                    <CartesianGrid stroke="#edf0ea" vertical={false} />
                    <XAxis dataKey="target_period" tickFormatter={(value) => new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" })} tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Area type="monotone" dataKey="predicted_qty" stroke="#236b4b" fill="url(#demand)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              ) : <div className="grid h-full place-items-center rounded-xl bg-canvas text-center text-sm text-slate-500">Forecasts appear after the demand evaluation script runs.</div>}
            </div>
            <div className="mt-4 border-t border-line pt-4">
              <p className="mb-2 text-sm font-semibold">Recommended for you</p>
              {recommendations.length ? recommendations.slice(0, 4).map((product) => (
                <div className="flex justify-between border-t border-line py-2 text-sm" key={product.id}><span>{product.name}</span><span className="text-slate-500">${product.unit_price.toFixed(2)}</span></div>
              )) : <p className="text-xs text-slate-500">Popular items appear once products are available.</p>}
            </div>
          </section>
        </div>

        <div className="mt-6 grid gap-6 lg:grid-cols-2">
          <section className="panel p-5">
            <SectionTitle title="Order activity" subtitle="Track your recent marketplace orders" />
            <div className="mt-3 space-y-2">
              {orders.slice(0, 8).map((order) => (
                <div className="flex items-center justify-between border-t border-line py-3" key={order.id}>
                  <div><p className="text-sm font-medium">Order #{order.id}</p><p className="text-xs text-slate-500">{new Date(order.order_date).toLocaleDateString()} · {order.items.length} line items</p></div>
                  <div className="flex items-center gap-3"><span className="text-sm font-medium">${order.total_amount.toFixed(2)}</span><StatusBadge status={order.status} />
                    {account.role === "wholesaler" && order.status === "pending" && <><button className="text-xs font-medium text-leaf" onClick={() => void decideOrder(order.id, "confirm")}>Confirm</button><button className="text-xs text-red-600" onClick={() => void decideOrder(order.id, "reject")}>Reject</button></>}
                  </div>
                </div>
              ))}
              {!orders.length && <p className="py-6 text-sm text-slate-500">No orders yet.</p>}
            </div>
          </section>
          <section className="panel p-5">
            <SectionTitle title="Operations & alerts" subtitle="Inventory thresholds and marketplace updates" />
            {summary.low_stock.slice(0, 5).map((item) => (
              <div className="mt-3 flex items-center justify-between rounded-xl bg-amber-50 px-3 py-2 text-sm" key={`${item.product_id}-${item.warehouse_code}`}>
                <span>{item.name}<span className="ml-2 text-xs text-amber-800">{item.warehouse_code}</span></span><span className="font-medium text-amber-800">{item.stock_qty} / {item.reorder_level}</span>
              </div>
            ))}
            <div className="mt-3 divide-y divide-line">
              {notices.slice(0, 6).map((item) => <div className="py-3 text-sm" key={item.id}><p>{item.message}</p><p className="mt-1 text-xs capitalize text-slate-400">{item.kind.replace("_", " ")}</p></div>)}
              {!notices.length && !summary.low_stock.length && <p className="py-5 text-sm text-slate-500">You’re all caught up.</p>}
            </div>
          </section>
        </div>

        <section className="panel mt-6 p-5">
          <SectionTitle title="Top products" subtitle="Best sellers by confirmed order units" />
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
            {summary.top_products.slice(0, 5).map((product, index) => (
              <div className="rounded-xl bg-canvas p-3" key={product.product_id}>
                <p className="text-xs text-slate-400">0{index + 1}</p><p className="mt-1 truncate text-sm font-medium">{product.name}</p><p className="mt-2 text-xs text-slate-500">{product.units_sold} units sold</p>
              </div>
            ))}
            {!summary.top_products.length && <p className="text-sm text-slate-500">Sales insights will appear as orders are confirmed.</p>}
          </div>
        </section>
      </main>
    </div>
  );
}

function Brand() {
  return <div className="flex items-center gap-2"><span className="grid h-9 w-9 place-items-center rounded-xl bg-leaf text-lg font-bold text-white">M</span><span className="text-lg font-semibold tracking-tight">marketwise</span></div>;
}

function SectionTitle({ title, subtitle }: { title: string; subtitle: string }) {
  return <div className="mb-4"><h2 className="font-semibold">{title}</h2><p className="mt-1 text-xs text-slate-500">{subtitle}</p></div>;
}

function StatCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="panel p-5"><p className="text-sm text-slate-500">{label}</p><p className="mt-2 text-3xl font-semibold tracking-tight">{value}</p><p className="mt-1 text-xs text-slate-400">{detail}</p></div>;
}

function StatusBadge({ status }: { status: string }) {
  const color = status === "confirmed" || status === "fulfilled" ? "bg-emerald-50 text-leaf" : status === "rejected" || status === "cancelled" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-800";
  return <span className={`rounded-full px-2.5 py-1 text-xs capitalize ${color}`}>{status}</span>;
}
