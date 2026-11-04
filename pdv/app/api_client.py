import requests


class ArmazemAPI:
    def __init__(self, base_url, access_token=None, empresa_id=None, timeout=8):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.empresa_id = str(empresa_id) if empresa_id else None
        self.session = requests.Session()
        self.refresh_token = None
        self.session.headers.update({"Accept": "application/json"})
        if access_token:
            self.session.headers["Authorization"] = f"Bearer {access_token}"
        if self.empresa_id:
            self.session.headers["X-Empresa-ID"] = self.empresa_id

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        if response.status_code == 401 and self.refresh_token:
            refresh = self.session.post(
                f"{self.base_url}/api/v1/auth/token/refresh/",
                json={"refresh": self.refresh_token},
                timeout=self.timeout,
            )
            if refresh.ok:
                tokens = refresh.json()
                self.session.headers["Authorization"] = f"Bearer {tokens['access']}"
                self.refresh_token = tokens.get("refresh", self.refresh_token)
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        if not response.ok:
            try:
                details = response.json()
                message = "; ".join(f"{key}: {value}" for key, value in details.items()) if isinstance(details, dict) else str(details)
            except ValueError:
                message = "O servidor não conseguiu concluir a operação."
            raise requests.HTTPError(message, response=response)
        return response

    def login(self, username, password):
        response = self.session.post(
            f"{self.base_url}/api/v1/auth/token/",
            json={"username": username, "password": password}, timeout=self.timeout,
        )
        response.raise_for_status()
        tokens = response.json()
        self.refresh_token = tokens.get("refresh")
        self.session.headers["Authorization"] = f"Bearer {tokens['access']}"
        return tokens

    def minhas_empresas(self):
        response = self._request("GET", "/api/v1/minhas-empresas/")
        return response.json()

    def selecionar_empresa(self, empresa_id):
        self.empresa_id = str(empresa_id)
        self.session.headers["X-Empresa-ID"] = self.empresa_id

    def empresa_info(self):
        response = self._request("GET", "/api/v1/empresa/")
        return response.json()

    def produtos(self):
        response = self._request("GET", "/api/v1/produtos/")
        return response.json()

    def clientes(self):
        response = self._request("GET", "/api/v1/clientes/")
        return response.json()

    def resumo_empresa(self):
        response = self._request("GET", "/api/v1/empresa/resumo/")
        return response.json()

    def abrir_caixa(self, dispositivo_id, saldo_abertura=0):
        response = self._request(
            "POST", "/api/v1/caixas/abrir/",
            json={"dispositivo_id": dispositivo_id, "saldo_abertura": str(saldo_abertura)},
        )
        return response.json()

    def resumo_caixa(self, caixa_id):
        response = self._request("GET", f"/api/v1/caixas/{caixa_id}/resumo/")
        return response.json()

    def movimentar_caixa(self, caixa_id, tipo, valor, motivo):
        response = self._request(
            "POST", "/api/v1/caixas/movimentacoes/",
            json={"caixa": caixa_id, "tipo": tipo, "valor": str(valor), "motivo": motivo},
        )
        return response.json()

    def fechar_caixa(self, caixa_id, saldo_fechamento):
        response = self._request(
            "POST", f"/api/v1/caixas/{caixa_id}/fechar/",
            json={"saldo_fechamento": str(saldo_fechamento)},
        )
        return response.json()

    def registrar_venda(self, payload):
        response = self._request("POST", "/api/v1/vendas/", json=payload)
        return response.json()

    def cancelar_venda(self, venda_id, token, justificativa=""):
        response = self._request(
            "POST", f"/api/v1/vendas/{venda_id}/cancelar/",
            json={"token": token, "justificativa": justificativa},
        )
        return response.json()


    def criar_pedido_web(self, cliente_nome, cliente_telefone, itens, endereco_entrega="", entrega_solicitada_em=""):
        response = self._request(
            "POST", "/api/v1/pedidos-web/",
            json={
                "cliente_nome": cliente_nome, "cliente_telefone": cliente_telefone,
                "endereco_entrega": endereco_entrega,
                "entrega_solicitada_em": entrega_solicitada_em,
                "itens": itens,
            },
        )
        return response.json()

    def pedidos_web(self):
        response = self._request("GET", "/api/v1/pedidos-web/")
        return response.json()

    def finalizar_pedido_web(self, pedido_id, caixa_id, pagamentos):
        response = self._request(
            "POST", f"/api/v1/pedidos-web/{pedido_id}/finalizar/",
            json={"caixa": caixa_id, "pagamentos": pagamentos},
        )
        return response.json()

    def cliente_cpf(self, cpf):
        return self._request('GET', '/api/v1/clientes/cpf/', params={'cpf': cpf}).json()

    def cadastrar_cliente_cpf(self, cpf, nome, telefone):
        return self._request('POST', '/api/v1/clientes/cpf/', json={'cpf': cpf, 'nome': nome, 'telefone': telefone}).json()

    def salvar_orcamento(self, payload):
        return self._request('POST', '/api/v1/orcamentos/', json=payload).json()

    def buscar_orcamentos(self, busca, tipo):
        return self._request('GET', '/api/v1/orcamentos/', params={'busca': busca, 'tipo': tipo}).json()
