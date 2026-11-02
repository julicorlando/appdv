import hashlib
import secrets
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class UsuarioPerfil(models.Model):
    class Papel(models.TextChoices):
        CAIXA = "CAIXA", "Caixa"
        GERENTE = "GERENTE", "Gerente"
        ADMIN = "ADMIN", "Administrador"
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil_armazem")
    papel = models.CharField(max_length=10, choices=Papel.choices, default=Papel.CAIXA)
    ativo = models.BooleanField(default=True)


class Categoria(models.Model):
    nome = models.CharField(max_length=120, unique=True)
    ativa = models.BooleanField(default=True)
    def __str__(self):
        return self.nome


class Produto(models.Model):
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name="produtos")
    codigo = models.CharField(max_length=50, unique=True)
    codigo_barras = models.CharField(max_length=50, blank=True, db_index=True)
    nome = models.CharField(max_length=200)
    unidade = models.CharField(max_length=12, default="UN")
    preco_venda = models.DecimalField(max_digits=12, decimal_places=2)
    custo_medio = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    estoque_atual = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    estoque_minimo = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    ativo = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"{self.codigo} — {self.nome}"


class Caixa(models.Model):
    class Estado(models.TextChoices):
        ABERTO = "ABERTO", "Aberto"
        FECHADO = "FECHADO", "Fechado"
    dispositivo_id = models.CharField(max_length=100, db_index=True)
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    aberto_em = models.DateTimeField(auto_now_add=True)
    fechado_em = models.DateTimeField(null=True, blank=True)
    saldo_abertura = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    saldo_informado_fechamento = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ABERTO)


class Venda(models.Model):
    class Estado(models.TextChoices):
        CONCLUIDA = "CONCLUIDA", "Concluída"
        CANCELADA = "CANCELADA", "Cancelada"
        PENDENTE = "PENDENTE", "Pendente de sincronização"
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True)
    caixa = models.ForeignKey(Caixa, on_delete=models.PROTECT, related_name="vendas")
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.CONCLUIDA)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    desconto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    realizada_em = models.DateTimeField(default=timezone.now)
    sincronizada_em = models.DateTimeField(auto_now_add=True)
    observacao = models.TextField(blank=True)


class ItemVenda(models.Model):
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
    venda = models.ForeignKey(Venda, on_delete=models.PROTECT, related_name="transacoes")
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.PAGAMENTO)
    meio = models.CharField(max_length=10, choices=Meio.choices)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    referencia_externa = models.CharField(max_length=120, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)


class TokenAutorizacao(models.Model):
    class Acao(models.TextChoices):
        CANCELAR_VENDA = "CANCELAR_VENDA", "Cancelar venda"
        ESTORNAR = "ESTORNAR", "Estornar valor"
        TROCAR = "TROCAR", "Trocar mercadoria"
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
        return secrets.token_urlsafe(8)

    def esta_valido(self):
        return self.usado_em is None and self.revogado_em is None and self.expira_em > timezone.now()
