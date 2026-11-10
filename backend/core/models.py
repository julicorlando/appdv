import hashlib
import secrets
import uuid
from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone


class Empresa(models.Model):
    frete_padrao = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    """Tenant isolado: cada empresa possui catálogo, estoque, caixa e vendas próprios."""
    nome = models.CharField(max_length=180)
    cnpj = models.CharField(max_length=14, blank=True, db_index=True)
    razao_social = models.CharField("Razão social", max_length=180, blank=True)
    inscricao_estadual = models.CharField("Inscrição estadual", max_length=20, blank=True)
    regime_tributario = models.CharField("Regime tributário", max_length=2, blank=True, choices=[("1", "Simples Nacional"), ("2", "Simples Nacional — excesso de sublimite"), ("3", "Regime normal"), ("4", "MEI")])
    logradouro = models.CharField(max_length=180, blank=True)
    numero = models.CharField("Número", max_length=20, blank=True)
    complemento = models.CharField(max_length=100, blank=True)
    bairro = models.CharField(max_length=100, blank=True)
    municipio = models.CharField("Município", max_length=100, blank=True)
    codigo_municipio = models.CharField("Código IBGE do município", max_length=7, blank=True)
    uf = models.CharField("UF", max_length=2, blank=True)
    cep = models.CharField("CEP", max_length=8, blank=True)
    telefone = models.CharField(max_length=30, blank=True)
    email = models.EmailField("E-mail", blank=True)
    ambiente_fiscal = models.CharField("Ambiente fiscal", max_length=12, default="HOMOLOGACAO", choices=[("HOMOLOGACAO", "Homologação"), ("PRODUCAO", "Produção")])
    provedor_fiscal = models.CharField("Provedor fiscal", max_length=100, blank=True)
    slug = models.SlugField(max_length=180, unique=True)
    ativa = models.BooleanField(default=True)
    logo = models.ImageField(upload_to="empresas/logos/", blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    def politica_pdv(self):
        policy=PoliticaDesconto.objects.filter(empresa=self).first()
        return {"modo":policy.modo,"limite_caixa":str(policy.limite_caixa),"percentual":str(policy.percentual),"meio":policy.meio,"minimo":str(policy.minimo)} if policy else {"modo":"MANUAL","limite_caixa":"5","percentual":"0","meio":"","minimo":"0"}

    def dados_documento(self):
        campos = ("nome", "razao_social", "cnpj", "inscricao_estadual", "regime_tributario", "logradouro", "numero", "complemento", "bairro", "municipio", "codigo_municipio", "uf", "cep", "telefone", "email")
        return {campo: getattr(self, campo) for campo in campos}

    @property
    def endereco_documento(self):
        return ", ".join(str(valor) for valor in (self.logradouro, self.numero, self.complemento, self.bairro, self.municipio, self.uf, self.cep) if valor)

    def __str__(self):
        return self.nome


class MembroEmpresa(models.Model):
    class Papel(models.TextChoices):
        CAIXA = "CAIXA", "Caixa"
        GERENTE = "GERENTE", "Gerente"
        ADMIN = "ADMIN", "Administrador"

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="membros")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="empresas_vinculadas")
    papel = models.CharField(max_length=10, choices=Papel.choices, default=Papel.CAIXA)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("empresa", "usuario"), name="uniq_usuario_empresa")]


class UsuarioPerfil(models.Model):
    """Preferências globais do usuário; papéis de negócio ficam em MembroEmpresa."""
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil_armazem")
    ativo = models.BooleanField(default=True)


class Categoria(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="categorias")
    nome = models.CharField(max_length=120)
    ativa = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("empresa", "nome"), name="uniq_categoria_empresa")]

    def __str__(self):
        return self.nome


class Produto(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="produtos")
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name="produtos")
    codigo = models.CharField(max_length=50)
    codigo_barras = models.CharField(max_length=50, blank=True, db_index=True)
    setor = models.CharField(max_length=80, blank=True, db_index=True)
    endereco = models.CharField(max_length=100, blank=True, db_index=True)
    nome = models.CharField(max_length=200)
    unidade = models.CharField(max_length=12, default="UN")
    unidade_estoque = models.CharField(max_length=12, blank=True, default="")
    fator_conversao = models.DecimalField(max_digits=12, decimal_places=6, default=1)
    preco_venda = models.DecimalField(max_digits=12, decimal_places=2)
    custo_medio = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    estoque_atual = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    estoque_minimo = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    ativo = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("empresa", "codigo"), name="uniq_codigo_produto_empresa"),
        ]
        indexes = [models.Index(fields=("empresa", "nome"), name="prod_tenant_name_idx"), models.Index(fields=("empresa", "codigo_barras"), name="prod_tenant_barcode_idx")]

    def __str__(self):
        return f"{self.codigo} — {self.nome}"


class Cliente(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="clientes")
    nome = models.CharField(max_length=180)
    documento = models.CharField(max_length=14, blank=True)
    telefone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    endereco = models.CharField(max_length=250, blank=True)
    limite_credito = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("nome",)
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "documento"), condition=~models.Q(documento=""),
                name="uniq_documento_cliente_empresa",
            ),
        ]

    def __str__(self):
        return self.nome


class Caixa(models.Model):
    class Estado(models.TextChoices):
        ABERTO = "ABERTO", "Aberto"
        FECHADO = "FECHADO", "Fechado"
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="caixas")
    dispositivo_id = models.CharField(max_length=100, db_index=True)
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    aberto_em = models.DateTimeField(auto_now_add=True)
    fechado_em = models.DateTimeField(null=True, blank=True)
    saldo_abertura = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    saldo_informado_fechamento = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    saldo_esperado_fechamento = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    diferenca_fechamento = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ABERTO)


class Venda(models.Model):
    frete = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    class Estado(models.TextChoices):
        CONCLUIDA = "CONCLUIDA", "Concluída"
        CANCELADA = "CANCELADA", "Cancelada"
        PENDENTE = "PENDENTE", "Pendente de sincronização"
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="vendas")
    idempotency_key = models.UUIDField(default=uuid.uuid4)
    caixa = models.ForeignKey(Caixa, on_delete=models.PROTECT, related_name="vendas")
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    cliente = models.ForeignKey("core.Cliente", null=True, blank=True, on_delete=models.PROTECT, related_name="vendas")
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.CONCLUIDA)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    realizada_em = models.DateTimeField(default=timezone.now)
    sincronizada_em = models.DateTimeField(auto_now_add=True)
    observacao = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("empresa", "idempotency_key"), name="uniq_idempotency_empresa")]


class ItemVenda(models.Model):
    custo_estoque_unitario = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="itens_venda")
    venda = models.ForeignKey(Venda, on_delete=models.PROTECT, related_name="itens")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    preco_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fator_conversao = models.DecimalField(max_digits=12, decimal_places=6, default=1)
    total = models.DecimalField(max_digits=12, decimal_places=2)


class Transacao(models.Model):
    class Tipo(models.TextChoices):
        PAGAMENTO = "PAGAMENTO", "Pagamento"
        ESTORNO = "ESTORNO", "Estorno"
    class Meio(models.TextChoices):
        DINHEIRO = "DINHEIRO", "Dinheiro"
        CARTAO = "CARTAO", "Cartão"
        PIX = "PIX", "PIX"
        OUTRO = "OUTRO", "Outro"
        LOJA = "LOJA", "Crédito em loja"
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="transacoes")
    venda = models.ForeignKey(Venda, on_delete=models.PROTECT, related_name="transacoes")
    # Caixa em que o dinheiro entrou ou saiu. Um estorno feito hoje pertence ao
    # caixa de hoje, mesmo que a venda tenha sido registrada em outro caixa.
    caixa = models.ForeignKey(Caixa, null=True, blank=True, on_delete=models.PROTECT, related_name="transacoes")
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.PAGAMENTO)
    meio = models.CharField(max_length=10, choices=Meio.choices)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    valor_recebido = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    troco = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    referencia_externa = models.CharField(max_length=120, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)


class TokenAutorizacao(models.Model):
    class Acao(models.TextChoices):
        CANCELAR_VENDA = "CANCELAR_VENDA", "Cancelar venda"
        ESTORNAR = "ESTORNAR", "Estornar valor"
        TROCAR = "TROCAR", "Trocar mercadoria"
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    acao = models.CharField(max_length=20, choices=Acao.choices)
    venda = models.ForeignKey(Venda, null=True, blank=True, on_delete=models.CASCADE)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tokens_criados")
    criado_em = models.DateTimeField(auto_now_add=True)
    expira_em = models.DateTimeField()
    usado_em = models.DateTimeField(null=True, blank=True)
    usado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="tokens_consumidos")
    revogado_em = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def hash_token(codigo):
        return hashlib.sha256(codigo.encode("utf-8")).hexdigest()

    @classmethod
    def gerar_codigo(cls):
        return secrets.token_urlsafe(16)

    def esta_valido(self):
        return self.usado_em is None and self.revogado_em is None and self.expira_em > timezone.now()


class MovimentacaoEstoque(models.Model):
    class Tipo(models.TextChoices):
        ENTRADA = "ENTRADA", "Entrada"
        VENDA = "VENDA", "Venda"
        DEVOLUCAO = "DEVOLUCAO", "Devolução"
        PERDA = "PERDA", "Perda"
        AJUSTE = "AJUSTE", "Ajuste"
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="movimentacoes_estoque")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name="movimentacoes")
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    referencia = models.CharField(max_length=120, blank=True)
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-criado_em",)


class InventarioContagem(models.Model):
    em_recontagem = models.BooleanField(default=False)
    revisada_em = models.DateTimeField(null=True, blank=True)
    class Estado(models.TextChoices):
        PROGRAMADO = "PROGRAMADO", "Programado"
        ABERTO = "ABERTO", "Em contagem"
        CONCLUIDO = "CONCLUIDO", "Concluído"

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="contagens")
    setor = models.CharField(max_length=80)
    data_programada = models.DateField(db_index=True)
    quantidade_itens = models.PositiveIntegerField(default=20)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PROGRAMADO)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="contagens_criadas")
    criado_em = models.DateTimeField(auto_now_add=True)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    concluido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("data_programada", "setor")
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "data_programada", "setor"),
                name="uniq_contagem_dia_setor_empresa",
            )
        ]


class ItemContagem(models.Model):
    primeira_quantidade = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    contagem = models.ForeignKey(InventarioContagem, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name="contagens")
    saldo_sistema = models.DecimalField(max_digits=14, decimal_places=3)
    quantidade_contada = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    diferenca = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    contado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    contado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("contagem", "produto"), name="uniq_produto_contagem")
        ]


class PedidoWeb(models.Model):
    frete = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    """Pedido criado no PDV e confirmado pelo cliente antes da venda no caixa."""
    class Estado(models.TextChoices):
        AGUARDANDO_CLIENTE = "AGUARDANDO_CLIENTE", "Aguardando cliente"
        CONFIRMADO = "CONFIRMADO", "Confirmado pelo cliente"
        SEPARANDO = "SEPARANDO", "Em separação"
        CONCLUIDO = "CONCLUIDO", "Venda finalizada"
        CANCELADO = "CANCELADO", "Cancelado"

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="pedidos_web")
    codigo_publico = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    cliente_nome = models.CharField(max_length=160)
    cliente_telefone = models.CharField(max_length=20)
    endereco_entrega = models.CharField(max_length=250, blank=True)
    entrega_solicitada_em = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.AGUARDANDO_CLIENTE, db_index=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="pedidos_web_criados")
    criado_em = models.DateTimeField(auto_now_add=True)
    expira_em = models.DateTimeField(db_index=True)
    confirmado_em = models.DateTimeField(null=True, blank=True)
    venda = models.ForeignKey("core.Venda", null=True, blank=True, on_delete=models.SET_NULL, related_name="pedido_web")
    observacao = models.TextField(blank=True)

    class Meta:
        ordering = ("-criado_em",)
        indexes = [models.Index(fields=("empresa", "estado", "criado_em"), name="pedido_tenant_status_idx")]

    def __str__(self):
        return f"Pedido web #{self.pk} — {self.cliente_nome}"


class ItemPedidoWeb(models.Model):
    pedido = models.ForeignKey(PedidoWeb, on_delete=models.CASCADE, related_name="itens")
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="itens_pedidos_web")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name="itens_pedidos_web")
    nome_produto = models.CharField(max_length=200)
    codigo_produto = models.CharField(max_length=50)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    preco_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    total = models.DecimalField(max_digits=12, decimal_places=2)

class Fornecedor(models.Model):
    """Fornecedor cadastrado dentro do tenant da empresa."""
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="fornecedores")
    nome = models.CharField(max_length=180)
    cnpj = models.CharField(max_length=14, blank=True)
    email = models.EmailField(blank=True)
    telefone = models.CharField(max_length=30, blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("nome",)
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "cnpj"), condition=~models.Q(cnpj=""),
                name="uniq_cnpj_fornecedor_empresa",
            ),
        ]

    def __str__(self):
        return self.nome


class CompraEntrada(models.Model):
    pedido_compra = models.ForeignKey("PedidoCompra", null=True, blank=True, on_delete=models.PROTECT, related_name="notas")
    """Nota de compra importada como rascunho; estoque só muda ao confirmar recebimento."""
    class Estado(models.TextChoices):
        RASCUNHO = "RASCUNHO", "Aguardando conferência"
        RECEBIDA = "RECEBIDA", "Recebida"
        CANCELADA = "CANCELADA", "Cancelada"

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="compras_entrada")
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, related_name="compras")
    numero_nf = models.CharField(max_length=20)
    serie = models.CharField(max_length=10, blank=True)
    chave_acesso = models.CharField(max_length=44, blank=True)
    emitente_cnpj = models.CharField(max_length=14, blank=True)
    data_emissao = models.DateField(null=True, blank=True)
    vencimento = models.DateField(null=True, blank=True, db_index=True)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.RASCUNHO, db_index=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="compras_importadas")
    recebido_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="compras_recebidas")
    criado_em = models.DateTimeField(auto_now_add=True)
    recebido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-criado_em",)
        constraints = [
            models.UniqueConstraint(
                fields=("empresa", "emitente_cnpj", "serie", "numero_nf"),
                condition=~models.Q(emitente_cnpj=""),
                name="uniq_nf_entrada_empresa_emitente",
            ),
            models.UniqueConstraint(
                fields=("empresa", "chave_acesso"),
                condition=~models.Q(chave_acesso=""),
                name="uniq_chave_nf_entrada_empresa",
            ),
        ]

    def __str__(self):
        return f"NF {self.numero_nf} — {self.fornecedor.nome}"


class ItemCompraEntrada(models.Model):
    compra = models.ForeignKey(CompraEntrada, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey(Produto, null=True, blank=True, on_delete=models.PROTECT, related_name="itens_compra")
    codigo_xml = models.CharField(max_length=60, blank=True)
    descricao_xml = models.CharField(max_length=240)
    unidade_xml = models.CharField(max_length=20, blank=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    custo_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    fator_conversao = models.DecimalField(max_digits=12, decimal_places=6, default=1)
    total = models.DecimalField(max_digits=14, decimal_places=2)



class ContaPagar(models.Model):
    class Estado(models.TextChoices):
        ABERTA = "ABERTA", "Em aberto"
        PARCIAL = "PARCIAL", "Parcialmente paga"
        QUITADA = "QUITADA", "Quitada"
        CANCELADA = "CANCELADA", "Cancelada"

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="contas_pagar")
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, related_name="contas_pagar")
    compra = models.OneToOneField(CompraEntrada, null=True, blank=True, on_delete=models.PROTECT, related_name="conta_pagar")
    descricao = models.CharField(max_length=240)
    valor_original = models.DecimalField(max_digits=14, decimal_places=2)
    saldo = models.DecimalField(max_digits=14, decimal_places=2)
    vencimento = models.DateField(db_index=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.ABERTA, db_index=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="contas_pagar_criadas")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("vencimento", "criado_em")


class PagamentoContaPagar(models.Model):
    conta = models.ForeignKey(ContaPagar, on_delete=models.PROTECT, related_name="pagamentos")
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="pagamentos_contas_pagar")
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    meio = models.CharField(max_length=10, choices=Transacao.Meio.choices)
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    referencia = models.CharField(max_length=120, blank=True)
    pago_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-pago_em",)


class MovimentacaoCaixa(models.Model):
    class Tipo(models.TextChoices):
        SANGRIA = "SANGRIA", "Sangria"
        SUPRIMENTO = "SUPRIMENTO", "Suprimento"

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="movimentacoes_caixa")
    caixa = models.ForeignKey(Caixa, on_delete=models.PROTECT, related_name="movimentacoes")
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    motivo = models.CharField(max_length=250)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-criado_em",)



class ContaReceber(models.Model):
    class Estado(models.TextChoices):
        ABERTA = "ABERTA", "Em aberto"
        PARCIAL = "PARCIAL", "Parcialmente paga"
        QUITADA = "QUITADA", "Quitada"
        CANCELADA = "CANCELADA", "Cancelada"

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="contas_receber")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="contas_receber")
    venda = models.OneToOneField(Venda, on_delete=models.PROTECT, related_name="conta_receber")
    valor_original = models.DecimalField(max_digits=12, decimal_places=2)
    saldo = models.DecimalField(max_digits=12, decimal_places=2)
    vencimento = models.DateField(db_index=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.ABERTA, db_index=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("vencimento", "criado_em")


class RecebimentoConta(models.Model):
    conta = models.ForeignKey(ContaReceber, on_delete=models.PROTECT, related_name="recebimentos")
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="recebimentos_contas")
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    caixa = models.ForeignKey(Caixa, null=True, blank=True, on_delete=models.PROTECT, related_name="recebimentos_conta")
    meio = models.CharField(max_length=10, choices=Transacao.Meio.choices)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    referencia = models.CharField(max_length=120, blank=True)
    recebido_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-recebido_em",)


class Orcamento(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='orcamentos')
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='orcamentos')
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    chave = models.UUIDField(default=uuid.uuid4)
    itens = models.JSONField(default=list)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    validade = models.DateField()
    criado_em = models.DateTimeField(auto_now_add=True)
    venda = models.OneToOneField(Venda, null=True, blank=True, on_delete=models.PROTECT, related_name='orcamento_origem')
    cancelado = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('empresa', 'chave'), name='uniq_orcamento_empresa_chave')]


class AprovacaoDesconto(models.Model):
    codigo = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='descontos_solicitados')
    aprovador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='descontos_aprovados')
    chave_operacao = models.UUIDField()
    resumo_itens = models.CharField(max_length=64)
    criada_em = models.DateTimeField(auto_now_add=True)
    venda = models.OneToOneField(Venda, null=True, blank=True, on_delete=models.PROTECT)


class EventoGestao(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    acao = models.CharField(max_length=80)
    referencia = models.CharField(max_length=120)
    detalhes = models.JSONField(default=dict)
    criado_em = models.DateTimeField(auto_now_add=True)


class Deposito(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    nome = models.CharField(max_length=100)
    principal = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=('empresa','nome'), name='uniq_deposito_nome'), models.UniqueConstraint(fields=('empresa',), condition=models.Q(principal=True), name='uniq_deposito_principal')]


class SaldoDeposito(models.Model):
    deposito = models.ForeignKey(Deposito, on_delete=models.PROTECT)
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    class Meta:
        constraints = [models.UniqueConstraint(fields=('deposito','produto'), name='uniq_saldo_deposito'), models.CheckConstraint(condition=models.Q(quantidade__gte=0), name='saldo_deposito_positivo')]


class OrdemEntrega(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    orcamento = models.OneToOneField(Orcamento, on_delete=models.PROTECT, related_name='entrega')
    venda = models.OneToOneField(Venda, null=True, blank=True, on_delete=models.PROTECT)
    estado = models.CharField(max_length=20, default='AGUARDANDO', choices=[('AGUARDANDO','Aguardando separação'),('PRONTO','Pronto para retirada/entrega'),('EM_ROTA','Em rota'),('PARCIAL','Entregue parcialmente'),('ENTREGUE','Entregue'),('CANCELADO','Cancelado')])
    endereco = models.CharField(max_length=250, blank=True)
    prevista = models.DateField(null=True, blank=True)
    responsavel = models.CharField(max_length=120, blank=True)
    frete = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    criado_em = models.DateTimeField(auto_now_add=True)


class ItemEntrega(models.Model):
    ordem = models.ForeignKey(OrdemEntrega, on_delete=models.CASCADE, related_name='itens')
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    deposito = models.ForeignKey(Deposito, on_delete=models.PROTECT)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    fator = models.DecimalField(max_digits=12, decimal_places=6, default=1)
    entregue = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(entregue__gte=0, entregue__lte=models.F('quantidade')), name='entrega_limites')]


class RegistroEntrega(models.Model):
    item = models.ForeignKey(ItemEntrega, on_delete=models.PROTECT)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    recebido_por = models.CharField(max_length=180)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    criado_em = models.DateTimeField(auto_now_add=True)


class DevolucaoItem(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    item = models.ForeignKey(ItemVenda, on_delete=models.PROTECT, related_name='devolucoes')
    chave = models.UUIDField(default=uuid.uuid4, unique=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    destino = models.CharField(max_length=12, choices=[('ESTOQUE','Retornar ao estoque'),('PERDA','Sem retorno ao estoque')])
    meio_estorno = models.CharField(max_length=10, default="OUTRO", choices=Transacao.Meio.choices)
    financeiro = models.CharField(max_length=12, choices=[('CREDITO','Crédito ao cliente'),('ESTORNO','Estorno registrado'),('ABATIMENTO','Abater conta a receber')])
    motivo = models.CharField(max_length=250)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    criado_em = models.DateTimeField(auto_now_add=True)


class CreditoLoja(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT)
    devolucao = models.OneToOneField(DevolucaoItem, null=True, on_delete=models.PROTECT)
    venda = models.ForeignKey(Venda, null=True, blank=True, on_delete=models.PROTECT)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    criado_em = models.DateTimeField(auto_now_add=True)


class PedidoCompra(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    estado = models.CharField(max_length=12, default='ABERTO', choices=[('ABERTO','Aberto'),('PARCIAL','Recebido parcialmente'),('RECEBIDO','Recebido'),('CANCELADO','Cancelado')])
    criado_em = models.DateTimeField(auto_now_add=True)


class ItemPedidoCompra(models.Model):
    pedido = models.ForeignKey(PedidoCompra, on_delete=models.CASCADE, related_name='itens')
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    recebido = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    custo = models.DecimalField(max_digits=14, decimal_places=4)
    class Meta:
        constraints = [models.UniqueConstraint(fields=('pedido','produto'), name='uniq_pedido_compra_produto')]


class PoliticaDesconto(models.Model):
    empresa = models.OneToOneField(Empresa, on_delete=models.CASCADE)
    modo = models.CharField(max_length=12, default='MANUAL', choices=[('MANUAL','Manual'),('AUTOMATICO','Automático'),('MISTO','Misto')])
    limite_caixa = models.DecimalField(max_digits=5, decimal_places=2, default=5, validators=[MinValueValidator(0)])
    percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    meio = models.CharField(max_length=10, blank=True, choices=[('','Qualquer pagamento'),('DINHEIRO','Dinheiro'),('PIX','Pix'),('CARTAO','Cartão')])
    minimo = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])


class TerminalPDV(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    identificador = models.CharField(max_length=120)
    pendentes = models.PositiveIntegerField(default=0)
    erros = models.TextField(blank=True)
    ultimo_contato = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=('empresa','identificador'), name='uniq_terminal_empresa')]


class DocumentoFiscal(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    venda = models.OneToOneField(Venda, on_delete=models.PROTECT)
    estado = models.CharField(max_length=16, default='CONFIGURAR', choices=[('CONFIGURAR','Configuração pendente'),('PENDENTE','Aguardando integração'),('AUTORIZADO','Autorizado'),('REJEITADO','Rejeitado'),('CANCELADO','Cancelado')])
    chave = models.CharField(max_length=44, blank=True)
    protocolo = models.CharField(max_length=80, blank=True)
    mensagem = models.TextField(blank=True)
    xml = models.FileField(upload_to='fiscal/xml/', blank=True)
    danfe = models.FileField(upload_to='fiscal/danfe/', blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)


class SessaoDesktopTicket(models.Model):
    resumo = models.CharField(max_length=64, unique=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    area = models.CharField(max_length=40, default='')
    expira_em = models.DateTimeField()
    usada_em = models.DateTimeField(null=True, blank=True)
