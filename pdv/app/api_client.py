import requests


class ArmazemAPI:
    def __init__(self, base_url, access_token=None, empresa_id=None, timeout=8):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.empresa_id = str(empresa_id) if empresa_id else None
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if access_token:
            self.session.headers["Authorization"] = f"Bearer {access_token}"
        if self.empresa_id:
            self.session.headers["X-Empresa-ID"] = self.empresa_id

    def login(self, username, password):
        response = self.session.post(
            f"{self.base_url}/api/v1/auth/token/",
            json={"username": username, "password": password}, timeout=self.timeout,
        )
        response.raise_for_status()
        tokens = response.json()
        self.session.headers["Authorization"] = f"Bearer {tokens['access']}"
        return tokens

    def minhas_empresas(self):
        response = self.session.get(f"{self.base_url}/api/v1/minhas-empresas/", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def selecionar_empresa(self, empresa_id):
        self.empresa_id = str(empresa_id)
        self.session.headers["X-Empresa-ID"] = self.empresa_id

    def empresa_info(self):
        response = self.session.get(f"{self.base_url}/api/v1/empresa/", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def produtos(self):
        response = self.session.get(f"{self.base_url}/api/v1/produtos/", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def abrir_caixa(self, dispositivo_id, saldo_abertura=0):
        response = self.session.post(
            f"{self.base_url}/api/v1/caixas/abrir/",
            json={"dispositivo_id": dispositivo_id, "saldo_abertura": str(saldo_abertura)},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def resumo_caixa(self, caixa_id):
        response = self.session.get(
            f"{self.base_url}/api/v1/caixas/{caixa_id}/resumo/", timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def movimentar_caixa(self, caixa_id, tipo, valor, motivo):
        response = self.session.post(
            f"{self.base_url}/api/v1/caixas/movimentacoes/",
            json={"caixa": caixa_id, "tipo": tipo, "valor": str(valor), "motivo": motivo},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def fechar_caixa(self, caixa_id, saldo_fechamento):
        response = self.session.post(
            f"{self.base_url}/api/v1/caixas/{caixa_id}/fechar/",
            json={"saldo_fechamento": str(saldo_fechamento)}, timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def registrar_venda(self, payload):
        response = self.session.post(f"{self.base_url}/api/v1/vendas/", json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def cancelar_venda(self, venda_id, token, justificativa=""):
        response = self.session.post(
            f"{self.base_url}/api/v1/vendas/{venda_id}/cancelar/",
            json={"token": token, "justificativa": justificativa},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()


    def criar_pedido_web(self, cliente_nome, cliente_telefone, itens):
        response = self.session.post(
            f"{self.base_url}/api/v1/pedidos-web/",
            json={"cliente_nome": cliente_nome, "cliente_telefone": cliente_telefone, "itens": itens},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def pedidos_web(self):
        response = self.session.get(f"{self.base_url}/api/v1/pedidos-web/", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def finalizar_pedido_web(self, pedido_id, caixa_id, pagamentos):
        response = self.session.post(
            f"{self.base_url}/api/v1/pedidos-web/{pedido_id}/finalizar/",
            json={"caixa": caixa_id, "pagamentos": pagamentos},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
