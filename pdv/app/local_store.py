import json
import sqlite3
import time
import uuid
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
            db.execute("""CREATE TABLE IF NOT EXISTS company_cache (
                tenant_id TEXT PRIMARY KEY, payload TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS customer_cache (
                tenant_id TEXT NOT NULL, cpf TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (tenant_id, cpf)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS checkout_attempts (
                tenant_id TEXT NOT NULL, operator TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (tenant_id, operator)
            )""")
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
            columns = {row[1] for row in db.execute("PRAGMA table_info(pending_sales)")}
            if "status" not in columns:
                # PENDENTE = reenvio automático; REJEITADA = o servidor recusou e
                # a venda precisa ser revisada (reenviar ou descartar com gerente).
                db.execute("ALTER TABLE pending_sales ADD COLUMN status TEXT NOT NULL DEFAULT 'PENDENTE'")
            if "ordem" not in columns:
                # Ordem global com as operações de caixa: abertura → vendas → sangrias → fechamento.
                db.execute("ALTER TABLE pending_sales ADD COLUMN ordem INTEGER NOT NULL DEFAULT 0")
            # Login sem internet: verificação PBKDF2 da senha (nunca a senha), válida por um prazo.
            db.execute("""CREATE TABLE IF NOT EXISTS offline_logins (
                server TEXT NOT NULL, username TEXT NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL,
                iterations INTEGER NOT NULL, refresh TEXT NOT NULL DEFAULT '', companies TEXT NOT NULL DEFAULT '[]',
                valid_until REAL NOT NULL, PRIMARY KEY (server, username)
            )""")
            # Abertura, sangria/suprimento e fechamento feitos sem internet.
            db.execute("""CREATE TABLE IF NOT EXISTS cash_ops (
                op_key TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, operator TEXT NOT NULL, kind TEXT NOT NULL,
                payload TEXT NOT NULL, ordem INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'PENDENTE',
                last_error TEXT NOT NULL DEFAULT ''
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS cash_map (
                tenant_id TEXT NOT NULL, local_id TEXT NOT NULL, server_id INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, local_id)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS current_cash (
                tenant_id TEXT NOT NULL, operator TEXT NOT NULL, ref TEXT NOT NULL,
                PRIMARY KEY (tenant_id, operator)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS sync_state (
                tenant_id TEXT PRIMARY KEY, products_cursor TEXT NOT NULL DEFAULT '', full_at REAL NOT NULL DEFAULT 0,
                full_day TEXT NOT NULL DEFAULT ''
            )""")

    def connect(self):
        # A sincronização roda em outra thread com a própria conexão; o timeout evita "database is locked".
        return sqlite3.connect(self.path, timeout=15)

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
                "INSERT OR IGNORE INTO pending_sales (idempotency_key, tenant_id, payload, last_error, ordem) VALUES (?, ?, ?, ?, ?)",
                (payload["idempotency_key"], str(tenant_id), json.dumps(payload), error[:500], time.time_ns()),
            )

    def pending_sales(self, tenant_id, include_rejected=False):
        """Vendas que o envio automático deve tentar; as recusadas só com ação do operador."""
        query = "SELECT idempotency_key, payload FROM pending_sales WHERE tenant_id=?"
        if not include_rejected:
            query += " AND status='PENDENTE'"
        with self.connect() as db:
            return db.execute(query + " ORDER BY ordem, created_at", (str(tenant_id),)).fetchall()

    def set_rejected(self, idempotency_key, error):
        with self.connect() as db:
            db.execute(
                "UPDATE pending_sales SET last_error=?, status='REJEITADA' WHERE idempotency_key=?",
                (str(error)[:500], idempotency_key),
            )

    def retry_sale(self, idempotency_key):
        with self.connect() as db:
            db.execute("UPDATE pending_sales SET status='PENDENTE' WHERE idempotency_key=?", (idempotency_key,))

    def pending_details(self, tenant_id, operator):
        with self.connect() as db:
            rows = db.execute(
                "SELECT idempotency_key, payload, last_error, status, created_at FROM pending_sales WHERE tenant_id=? ORDER BY created_at",
                (str(tenant_id),),
            ).fetchall()
        result = []
        for key, payload, error, status, created_at in rows:
            data = json.loads(payload)
            if data.get('operador_local') == operator:
                result.append({'chave': key, 'payload': data, 'erro': error, 'status': status, 'criada_em': created_at})
        return result

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

    def save_checkout_attempt(self, tenant_id, operator, payload):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO checkout_attempts (tenant_id, operator, payload) VALUES (?, ?, ?)", (str(tenant_id), str(operator), json.dumps(payload)))

    def checkout_attempt(self, tenant_id, operator):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM checkout_attempts WHERE tenant_id=? AND operator=?", (str(tenant_id), str(operator))).fetchone()
        return json.loads(row[0]) if row else None

    def clear_checkout_attempt(self, tenant_id, operator):
        with self.connect() as db:
            db.execute("DELETE FROM checkout_attempts WHERE tenant_id=? AND operator=?", (str(tenant_id), str(operator)))

    def save_company(self, tenant_id, payload):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO company_cache VALUES (?, ?)", (str(tenant_id), json.dumps(payload)))

    def company(self, tenant_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM company_cache WHERE tenant_id=?", (str(tenant_id),)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_customer(self, tenant_id, payload):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO customer_cache VALUES (?, ?, ?)", (str(tenant_id), payload['documento'], json.dumps(payload)))

    def customer(self, tenant_id, cpf):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM customer_cache WHERE tenant_id=? AND cpf=?", (str(tenant_id), cpf)).fetchone()
        return json.loads(row[0]) if row else None

    def pending_errors(self, tenant_id, operator):
        with self.connect() as db:
            rows = db.execute("SELECT idempotency_key, payload, last_error FROM pending_sales WHERE tenant_id=? ORDER BY created_at", (str(tenant_id),)).fetchall()
        return [(key, error) for key, payload, error in rows if json.loads(payload).get('operador_local') == operator]


    # ------------------------------------------------------------ catálogo incremental
    def replace_products(self, tenant_id, products):
        """Carga completa: o que não veio (inativado ou excluído) sai do caixa."""
        with self.connect() as db:
            db.execute("DELETE FROM products WHERE tenant_id=?", (str(tenant_id),))
            for product in products:
                db.execute("INSERT INTO products (tenant_id, product_id, payload) VALUES (?, ?, ?)",
                           (str(tenant_id), int(product["id"]), json.dumps(product, ensure_ascii=False)))

    def apply_product_changes(self, tenant_id, products):
        with self.connect() as db:
            for product in products:
                if product.get("ativo", True):
                    db.execute("INSERT OR REPLACE INTO products (tenant_id, product_id, payload, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                               (str(tenant_id), int(product["id"]), json.dumps(product, ensure_ascii=False)))
                else:
                    db.execute("DELETE FROM products WHERE tenant_id=? AND product_id=?", (str(tenant_id), int(product["id"])))

    def sync_state(self, tenant_id):
        with self.connect() as db:
            row = db.execute("SELECT products_cursor, full_at, full_day FROM sync_state WHERE tenant_id=?", (str(tenant_id),)).fetchone()
        return {"cursor": row[0], "full_at": row[1], "full_day": row[2]} if row else {"cursor": "", "full_at": 0, "full_day": ""}

    def save_sync_state(self, tenant_id, cursor, full_at=None, full_day=None):
        atual = self.sync_state(tenant_id)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO sync_state (tenant_id, products_cursor, full_at, full_day) VALUES (?, ?, ?, ?)",
                       (str(tenant_id), cursor or atual["cursor"], atual["full_at"] if full_at is None else full_at,
                        atual["full_day"] if full_day is None else full_day))

    def save_customers(self, tenant_id, customers):
        with self.connect() as db:
            for customer in customers:
                if customer.get("documento"):
                    db.execute("INSERT OR REPLACE INTO customer_cache VALUES (?, ?, ?)",
                               (str(tenant_id), customer["documento"], json.dumps(customer)))

    # --------------------------------------------------------------- caixa offline
    def queue_cash_op(self, tenant_id, operator, kind, payload):
        key = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO cash_ops (op_key, tenant_id, operator, kind, payload, ordem) VALUES (?, ?, ?, ?, ?, ?)",
                       (key, str(tenant_id), str(operator), kind, json.dumps(payload), time.time_ns()))
        return key

    def pending_cash_ops(self, tenant_id, operator):
        with self.connect() as db:
            rows = db.execute("SELECT op_key, kind, payload, ordem, status, last_error FROM cash_ops WHERE tenant_id=? AND operator=? ORDER BY ordem",
                              (str(tenant_id), str(operator))).fetchall()
        return [{"chave": r[0], "tipo": r[1], "payload": json.loads(r[2]), "ordem": r[3], "status": r[4], "erro": r[5]} for r in rows]

    def finish_cash_op(self, op_key):
        with self.connect() as db:
            db.execute("DELETE FROM cash_ops WHERE op_key=?", (op_key,))

    def set_cash_op_error(self, op_key, error, rejected=False):
        with self.connect() as db:
            db.execute("UPDATE cash_ops SET last_error=?, status=? WHERE op_key=?",
                       (str(error)[:500], "REJEITADA" if rejected else "PENDENTE", op_key))

    def map_cash(self, tenant_id, local_id, server_id):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO cash_map VALUES (?, ?, ?)", (str(tenant_id), str(local_id), int(server_id)))

    def resolve_cash(self, tenant_id, local_id):
        with self.connect() as db:
            row = db.execute("SELECT server_id FROM cash_map WHERE tenant_id=? AND local_id=?", (str(tenant_id), str(local_id))).fetchone()
        return row[0] if row else None

    def set_current_cash(self, tenant_id, operator, ref):
        with self.connect() as db:
            if ref is None:
                db.execute("DELETE FROM current_cash WHERE tenant_id=? AND operator=?", (str(tenant_id), str(operator)))
            else:
                db.execute("INSERT OR REPLACE INTO current_cash VALUES (?, ?, ?)", (str(tenant_id), str(operator), str(ref)))

    def current_cash(self, tenant_id, operator):
        with self.connect() as db:
            row = db.execute("SELECT ref FROM current_cash WHERE tenant_id=? AND operator=?", (str(tenant_id), str(operator))).fetchone()
        return row[0] if row else None

    def pending_count(self, tenant_id, operator):
        vendas = sum(1 for _, raw in self.pending_sales(tenant_id, include_rejected=True) if json.loads(raw).get("operador_local") == operator)
        return vendas + len(self.pending_cash_ops(tenant_id, operator))

    def rejected_sales(self, tenant_id, operator):
        with self.connect() as db:
            rows = db.execute("SELECT idempotency_key, payload FROM pending_sales WHERE tenant_id=? AND status='REJEITADA'", (str(tenant_id),)).fetchall()
        return [key for key, raw in rows if json.loads(raw).get("operador_local") == operator]

    # --------------------------------------------------------------- login offline
    def save_offline_login(self, server, username, record):
        with self.connect() as db:
            atual = db.execute("SELECT companies FROM offline_logins WHERE server=? AND username=?", (server, username)).fetchone()
            db.execute("INSERT OR REPLACE INTO offline_logins VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (server, username, record["salt"], record["hash"], record["iterations"], record.get("refresh", ""),
                        record.get("companies") or (atual[0] if atual else "[]"), record["valid_until"]))

    def save_offline_companies(self, server, username, companies):
        with self.connect() as db:
            db.execute("UPDATE offline_logins SET companies=? WHERE server=? AND username=?",
                       (json.dumps(companies, ensure_ascii=False), server, username))

    def offline_login(self, server, username):
        with self.connect() as db:
            row = db.execute("SELECT salt, hash, iterations, refresh, companies, valid_until FROM offline_logins WHERE server=? AND username=?",
                             (server, username)).fetchone()
        if not row:
            return None
        return {"salt": row[0], "hash": row[1], "iterations": row[2], "refresh": row[3],
                "companies": json.loads(row[4] or "[]"), "valid_until": row[5]}

    def forget_offline_login(self, server, username):
        with self.connect() as db:
            db.execute("DELETE FROM offline_logins WHERE server=? AND username=?", (server, username))
