import requests

class ArmazemAPI:
    def __init__(self, base_url, access_token=None, timeout=8):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if access_token:
            self.session.headers["Authorization"] = f"Bearer {access_token}"

    def produtos(self):
        response = self.session.get(f"{self.base_url}/api/v1/produtos/", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def registrar_venda(self, payload):
        response = self.session.post(f"{self.base_url}/api/v1/vendas/", json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def cancelar_venda(self, venda_id, token, justificativa):
        # Endpoint de escrita deve validar o token e consumi-lo atomicamente no backend.
        response = self.session.post(
            f"{self.base_url}/api/v1/vendas/{venda_id}/cancelar/",
            json={"token": token, "justificativa": justificativa},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
