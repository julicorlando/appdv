import json
import sqlite3
from pathlib import Path
from platformdirs import user_data_dir


def database_path():
    folder = Path(user_data_dir("ArmazemPDV", "ArmazemPDV"))
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "pdv-local.sqlite3"


class LocalStore:
    def __init__(self, path=None):
        self.path = str(path or database_path())
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS products (
                tenant_id TEXT NOT NULL, product_id INTEGER NOT NULL,
                payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, product_id)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS pending_sales (
                idempotency_key TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_error TEXT NOT NULL DEFAULT ''
            )""")

    def connect(self):
        return sqlite3.connect(self.path)

    def save_products(self, tenant_id, products):
        with self.connect() as db:
            for product in products:
                db.execute(
                    "INSERT OR REPLACE INTO products (tenant_id, product_id, payload, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                    (str(tenant_id), int(product["id"]), json.dumps(product, ensure_ascii=False)),
                )

    def list_products(self, tenant_id):
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM products WHERE tenant_id=? ORDER BY product_id", (str(tenant_id),)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def queue_sale(self, tenant_id, payload, error="Sem conexão com o servidor"):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO pending_sales (idempotency_key, tenant_id, payload, last_error) VALUES (?, ?, ?, ?)",
                (payload["idempotency_key"], str(tenant_id), json.dumps(payload), error[:500]),
            )

    def pending_sales(self, tenant_id):
        with self.connect() as db:
            return db.execute(
                "SELECT idempotency_key, payload FROM pending_sales WHERE tenant_id=? ORDER BY created_at",
                (str(tenant_id),),
            ).fetchall()

    def mark_synced(self, idempotency_key):
        with self.connect() as db:
            db.execute("DELETE FROM pending_sales WHERE idempotency_key=?", (idempotency_key,))

    def set_error(self, idempotency_key, error):
        with self.connect() as db:
            db.execute(
                "UPDATE pending_sales SET last_error=? WHERE idempotency_key=?",
                (str(error)[:500], idempotency_key),
            )
