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
                self.offline_login = False
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

    def produtos(self, desde=None):
        """Catálogo ativo; com `desde`, só o que mudou depois desse instante (inclui inativados)."""
        params = {"desde": desde} if desde else None
        response = self._request("GET", "/api/v1/produtos/", params=params)
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

    def movimentar_caixa(self, caixa_id, tipo, valor, motivo, chave=None):
        corpo = {"caixa": caixa_id, "tipo": tipo, "valor": str(valor), "motivo": motivo}
        if chave:
            corpo["chave"] = chave
        response = self._request("POST", "/api/v1/caixas/movimentacoes/", json=corpo)
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
        response = self._request('GET', '/api/v1/clientes/cpf/', params={'cpf': cpf})
        # Older servers returned an empty 200 response for an unknown CPF.
        if not response.content.strip():
            return None
        try:
            data = response.json()
        except ValueError as exc:
            raise ValueError("O servidor retornou uma resposta inválida ao consultar o CPF. Tente novamente.") from exc
        if data is None or (isinstance(data, dict) and data.get('encontrado') is False):
            return None
        if not isinstance(data, dict) or not data.get('id') or not data.get('nome'):
            raise ValueError("O servidor não retornou um cadastro de cliente válido.")
        return data

    def cadastrar_cliente_cpf(self, cpf, nome, telefone):
        return self._request('POST', '/api/v1/clientes/cpf/', json={'cpf': cpf, 'nome': nome, 'telefone': telefone}).json()

    def salvar_orcamento(self, payload):
        return self._request('POST', '/api/v1/orcamentos/', json=payload).json()

    def buscar_orcamentos(self, busca, tipo):
        return self._request('GET', '/api/v1/orcamentos/', params={'busca': busca, 'tipo': tipo}).json()

    def autorizar_desconto(self, chave, itens, usuario, senha):
        return self._request('POST', '/api/v1/descontos/autorizar/', json={
            'chave': chave, 'itens': itens, 'usuario': usuario, 'senha': senha}).json()['codigo']

    def heartbeat(self, pendentes, erros=''):
        import socket
        from app import VERSION
        return self._request('POST','/api/v1/terminais/heartbeat/',json={'identificador':socket.gethostname() + ':' + self.username,'pendentes':pendentes,'erros':erros,'versao':VERSION}).json()

    def versao_pdv(self):
        return self._request('GET', '/api/v1/pdv/versao/').json()

    def alterar_senha(self, senha_atual, nova_senha):
        return self._request('POST', '/api/v1/conta/senha/', json={'senha_atual': senha_atual, 'nova_senha': nova_senha}).json()

    def pix_criar(self, valor, chave):
        return self._request('POST', '/api/v1/pix/cobrancas/', json={'valor': str(valor), 'chave': chave}).json()

    def pix_consultar(self, cobranca_id):
        return self._request('GET', f'/api/v1/pix/cobrancas/{int(cobranca_id)}/').json()

    def point_criar(self, valor, tipo, chave):
        return self._request('POST', '/api/v1/point/cobrancas/', json={'valor': str(valor), 'tipo': tipo, 'chave': chave}).json()

    def point_consultar(self, intencao_id):
        return self._request('GET', f'/api/v1/point/cobrancas/{intencao_id}/').json()

    def point_cancelar(self, intencao_id):
        return self._request('DELETE', f'/api/v1/point/cobrancas/{intencao_id}/').json()

    def nfce_emitir(self, venda_id):
        return self._request('POST', f'/api/v1/vendas/{int(venda_id)}/nfce/', json={}).json()

    def nfce_consultar(self, venda_id):
        return self._request('GET', f'/api/v1/vendas/{int(venda_id)}/nfce/').json()

    def descartar_venda_offline(self, chave, usuario, senha, justificativa, erro='', venda=None):
        return self._request('POST', '/api/v1/vendas-offline/descartar/', json={
            'chave': chave, 'usuario': usuario, 'senha': senha, 'justificativa': justificativa,
            'erro': erro, 'venda': venda or {}}).json()

    def iniciar_gestao(self, area='painel'):
        return self._request('POST','/api/v1/desktop/session/',json={'area':area}).json()
