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

    def selecionar_empresa(self, empresa_id):
        """Define o tenant ativo após o operador escolher uma empresa autorizada."""
        self.empresa_id = str(empresa_id)
        self.session.headers["X-Empresa-ID"] = self.empresa_id

    def minhas_empresas(self):
        response = self.session.get(f"{self.base_url}/api/v1/minhas-empresas/", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def produtos(self):
        response = self.session.get(f"{self.base_url}/api/v1/produtos/", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def registrar_venda(self, payload):
        response = self.session.post(f"{self.base_url}/api/v1/vendas/", json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def cancelar_venda(self, venda_id, token, justificativa):
        # Requer conectividade; o servidor valida e consome o token atomicamente.
        response = self.session.post(
            f"{self.base_url}/api/v1/vendas/{venda_id}/cancelar/",
            json={"token": token, "justificativa": justificativa},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
