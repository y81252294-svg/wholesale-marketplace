# Machine Learning-Based Wholesale–Retail Marketplace

A full-stack wholesale marketplace with role-based account approval, product and inventory management, pack-aware ordering, demand prediction, and hybrid product recommendations. The seed data is synthetic and is explicitly marked as such.

## Project structure

```text
backend/
  api.py, main.py, database.py, models.py, schemas.py, security.py
  migrations/                 Alembic environment and initial schema
frontend/
  src/                        React + TypeScript marketplace dashboard
ml/
  recommendation.py            Content-based + item-based collaborative hybrid
  demand.py                    Demand features, model comparison, LSTM gate
  metrics.py, preprocessing.py, inventory_planning.py
data/
  seed.py                      Synthetic data generator + database seeder
  import_csv.py                Real order-line CSV validation
results/                       Generated evaluation CSV/JSON (not fabricated)
tests/                         API and ML unit tests
requirements.txt
docker-compose.yml
```

## Architecture

```mermaid
flowchart LR
  Retailer[Retailer browser] --> UI[React + Vite dashboard]
  Wholesaler[Wholesaler browser] --> UI
  Admin[Admin browser] --> UI
  UI -->|REST + JWT| API[FastAPI]
  API --> ORM[SQLAlchemy models]
  ORM --> DB[(PostgreSQL / SQLite)]
  API --> Rec[Hybrid recommendation service]
  API --> Forecast[Demand forecast service]
  Rec --> Train[Offline evaluation scripts]
  Forecast --> Train
  Train --> Results[results/*.csv + *.json]
  Seed[Synthetic seed / CSV import] --> DB
  Seed --> Data[products.csv + order_lines.csv]
  Data --> Train
```

## Run locally (Windows PowerShell)

Python 3.11, Node.js 20+, npm, and (for Docker) Docker Compose are required.

1. Create and activate an environment, then install dependencies:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

   XGBoost is included for the full model comparison. TensorFlow is installed from `requirements.txt` on Linux (including WSL2) because TensorFlow 2.19 does not provide a native Windows wheel; LSTM is skipped automatically when history is too short or TensorFlow is unavailable.

2. Optionally configure PostgreSQL. SQLite is the default:

   ```powershell
   $env:DATABASE_URL = "sqlite:///./marketplace.db"
   $env:JWT_SECRET_KEY = "replace-this-for-any-shared-or-deployed-environment"
   ```

   For PostgreSQL, set `DATABASE_URL` to a SQLAlchemy URL such as
   `postgresql+psycopg://user:password@localhost:5432/marketplace`.

3. Apply the schema and generate synthetic sample data:

   ```powershell
   alembic upgrade head
   python -m data.seed
   ```

   Generated CSV files are labelled synthetic. The seeder creates 50 retailer accounts, 5 wholesaler accounts, 300 products, about two years of weekly order activity, and a demo administrator. All seeded accounts use `SyntheticDemo123!`; seed users are already approved. To replace the current local database and generated CSVs, use `python -m data.seed --reset`.

4. Start the API in one terminal:

   ```powershell
   uvicorn backend.main:app --reload
   ```

   API docs: <http://localhost:8000/docs>. Health check: <http://localhost:8000/health>.

5. Start the web app in another terminal:

   ```powershell
   Set-Location frontend
   npm install
   npm run dev
   ```

   Open <http://localhost:5173>. The API URL defaults to `http://localhost:8000`; override with `VITE_API_URL` before building if needed.

## Run with Docker Compose

From the project root:

```powershell
docker compose up --build
```

Then, in another terminal, initialize and seed the PostgreSQL database:

```powershell
docker compose exec api alembic upgrade head
docker compose exec api python -m data.seed
```

The web UI is at <http://localhost:5173>; the API is at <http://localhost:8000>. The compose credentials and JWT signing key are for local development only—replace them before deployment.

## Accounts and marketplace flows

- Register as a retailer or wholesaler from the UI. A new account cannot sign in until an administrator approves it.
- For the demo environment, sign in with `synthetic-admin@example.test` / `SyntheticDemo123!` to approve registrations, or use one of the seeded retailer and wholesaler accounts.
- Retailers can search and filter the catalog, add product quantities using each product's minimum and pack multiple, place supplier-specific orders, see recommendations, and track status.
- Wholesalers can publish/edit products, update stock, and confirm or reject pending orders.
- Notifications are persisted for order status changes and inventory at/below reorder level.
- Admin registration is intentionally disabled; provision an admin account operationally rather than exposing admin signup.

## ML evaluation and results

Generate `data/products.csv`, `data/order_lines.csv`, and `data/synthetic_orders.csv` with `python -m data.seed` (the seed command also loads the database).

Run the two evaluations from the project root:

```powershell
python -m ml.evaluate_recommendations
python -m ml.evaluate_demand
```

Each command prints its measured comparison table and writes results to `results/`. No accuracy scores are prefilled. Recommendation evaluation uses a chronological train/validation/test split, tunes hybrid alpha on validation data, and compares Precision@K, Recall@K, F1@K, and NDCG@K against popularity. Demand evaluation preserves zero-demand product-weeks, uses chronological splits, compares seasonal-naive/moving-average baselines with ExtraTrees, RandomForest, and XGBoost, and conditionally evaluates an LSTM. MAPE excludes actual-zero points and records the excluded-point count. The LSTM feature and target scalers are fit on training data only.

`GET /forecast/{product_id}?horizon=4` returns stored forecast rows when present; otherwise it provides a seasonal-naive forecast from persisted, fulfilled/confirmed orders (moving-average fallback for shorter histories). `GET /recommendations/{retailer_id}?k=10` serves stored recommendations when available and has a popularity cold-start fallback. `ml/recommendation.py` provides the content, collaborative, hybrid, and alpha-selection implementation for offline evaluation and future scheduled recommendation refreshes.

The safety-stock helper is:

```python
from ml.inventory_planning import inventory_plan
inventory_plan(forecast_weekly_demand=14, demand_error_std=3,
               lead_time_weeks=2, z_score=1.65)
```

It calculates `SS = z * s_e * sqrt(L)` and `ROP = d_f * L + SS`.

## Real CSV import

The `data/import_csv.py` utility validates an order-line CSV using the same `product_id`, `retailer_id`, `date`, `quantity`, and `unit_price` columns. Use `--persist` to insert those historical lines as fulfilled orders. IDs must already exist in the marketplace database; optional `order_id` values group source lines, otherwise retailer/product-supplier/date groups are imported as orders.

```powershell
python -m data.import_csv path\to\order_lines.csv --persist
```

It also writes a `.validated.csv` file after type and value validation. Resolve external IDs to marketplace database IDs before importing them into the application database; raw CSV import never silently invents or maps entities.

## Tests

```powershell
python -m pytest -q
```

Tests cover API health, role approval/login/order workflows, weekly zero-fill preprocessing, leakage-safe chronological splitting, recommendation ranking metrics, and regression metrics including MAPE with zero actuals.

## Security notes

Passwords are bcrypt-hashed, API access tokens are signed JWTs, and every write/action route enforces role and ownership checks. Set a unique `JWT_SECRET_KEY`, enforce HTTPS, use managed secrets, and replace all sample credentials before deploying.
