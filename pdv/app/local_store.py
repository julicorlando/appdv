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
            db.execute("""CREATE TABLE IF NOT EXISTS held_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
                label TEXT NOT NULL, payload TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
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

    def save_receipt(self, sale_id, text):
        folder = Path(self.path).parent / "comprovantes"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"cupom-{sale_id}.html"
        path.write_text(text, encoding="utf-8")
        return path


    def hold_sale(self, tenant_id, label, payload):
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO held_sales (tenant_id, label, payload) VALUES (?, ?, ?)",
                (str(tenant_id), str(label)[:100], json.dumps(payload, ensure_ascii=False)),
            )
            return cursor.lastrowid

    def held_sales(self, tenant_id):
        with self.connect() as db:
            return db.execute(
                "SELECT id, label, payload, created_at FROM held_sales WHERE tenant_id=? ORDER BY created_at",
                (str(tenant_id),),
            ).fetchall()

    def remove_held_sale(self, sale_id, tenant_id):
        with self.connect() as db:
            db.execute(
                "DELETE FROM held_sales WHERE id=? AND tenant_id=?",
                (int(sale_id), str(tenant_id)),
            )
