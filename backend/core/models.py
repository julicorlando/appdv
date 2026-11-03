import hashlib
import secrets
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class Empresa(models.Model):
    """Tenant isolado: cada empresa possui catálogo, estoque, caixa e vendas próprios."""
    nome = models.CharField(max_length=180)
    cnpj = models.CharField(max_length=14, blank=True, db_index=True)
    slug = models.SlugField(max_length=180, unique=True)
    ativa = models.BooleanField(default=True)
    logo = models.ImageField(upload_to="empresas/logos/", blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

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
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="itens_venda")
    venda = models.ForeignKey(Venda, on_delete=models.PROTECT, related_name="itens")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    preco_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
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
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="transacoes")
    venda = models.ForeignKey(Venda, on_delete=models.PROTECT, related_name="transacoes")
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
    meio = models.CharField(max_length=10, choices=Transacao.Meio.choices)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    referencia = models.CharField(max_length=120, blank=True)
    recebido_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-recebido_em",)
