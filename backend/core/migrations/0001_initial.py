# Generated for the first tenant-aware release.
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="Empresa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=180)),
                ("cnpj", models.CharField(blank=True, db_index=True, max_length=14)),
                ("slug", models.SlugField(max_length=180, unique=True)),
                ("ativa", models.BooleanField(default=True)),
                ("criada_em", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="UsuarioPerfil",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ativo", models.BooleanField(default=True)),
                ("usuario", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="perfil_armazem", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="MembroEmpresa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("papel", models.CharField(choices=[("CAIXA", "Caixa"), ("GERENTE", "Gerente"), ("ADMIN", "Administrador")], default="CAIXA", max_length=10)),
                ("ativo", models.BooleanField(default=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="membros", to="core.empresa")),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="empresas_vinculadas", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="Categoria",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=120)),
                ("ativa", models.BooleanField(default=True)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="categorias", to="core.empresa")),
            ],
        ),
        migrations.CreateModel(
            name="Produto",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo", models.CharField(max_length=50)),
                ("codigo_barras", models.CharField(blank=True, db_index=True, max_length=50)),
                ("nome", models.CharField(max_length=200)),
                ("unidade", models.CharField(default="UN", max_length=12)),
                ("preco_venda", models.DecimalField(decimal_places=2, max_digits=12)),
                ("custo_medio", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("estoque_atual", models.DecimalField(decimal_places=3, default=0, max_digits=14)),
                ("estoque_minimo", models.DecimalField(decimal_places=3, default=0, max_digits=14)),
                ("ativo", models.BooleanField(default=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("categoria", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="produtos", to="core.categoria")),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="produtos", to="core.empresa")),
            ],
        ),
        migrations.CreateModel(
            name="Caixa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("dispositivo_id", models.CharField(db_index=True, max_length=100)),
                ("aberto_em", models.DateTimeField(auto_now_add=True)),
                ("fechado_em", models.DateTimeField(blank=True, null=True)),
                ("saldo_abertura", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("saldo_informado_fechamento", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("estado", models.CharField(choices=[("ABERTO", "Aberto"), ("FECHADO", "Fechado")], default="ABERTO", max_length=10)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="caixas", to="core.empresa")),
                ("operador", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="Venda",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("idempotency_key", models.UUIDField(default=uuid.uuid4)),
                ("estado", models.CharField(choices=[("CONCLUIDA", "Concluída"), ("CANCELADA", "Cancelada"), ("PENDENTE", "Pendente de sincronização")], default="CONCLUIDA", max_length=12)),
                ("subtotal", models.DecimalField(decimal_places=2, max_digits=12)),
                ("desconto", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("total", models.DecimalField(decimal_places=2, max_digits=12)),
                ("realizada_em", models.DateTimeField(default=django.utils.timezone.now)),
                ("sincronizada_em", models.DateTimeField(auto_now_add=True)),
                ("observacao", models.TextField(blank=True)),
                ("caixa", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="vendas", to="core.caixa")),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="vendas", to="core.empresa")),
                ("operador", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="ItemVenda",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantidade", models.DecimalField(decimal_places=3, max_digits=14)),
                ("preco_unitario", models.DecimalField(decimal_places=2, max_digits=12)),
                ("desconto", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("total", models.DecimalField(decimal_places=2, max_digits=12)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="itens_venda", to="core.empresa")),
                ("produto", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.produto")),
                ("venda", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="itens", to="core.venda")),
            ],
        ),
        migrations.CreateModel(
            name="Transacao",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo", models.CharField(choices=[("PAGAMENTO", "Pagamento"), ("ESTORNO", "Estorno")], default="PAGAMENTO", max_length=10)),
                ("meio", models.CharField(choices=[("DINHEIRO", "Dinheiro"), ("CARTAO", "Cartão"), ("PIX", "PIX"), ("OUTRO", "Outro")], max_length=10)),
                ("valor", models.DecimalField(decimal_places=2, max_digits=12)),
                ("referencia_externa", models.CharField(blank=True, max_length=120)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="transacoes", to="core.empresa")),
                ("venda", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transacoes", to="core.venda")),
            ],
        ),
        migrations.CreateModel(
            name="TokenAutorizacao",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("acao", models.CharField(choices=[("CANCELAR_VENDA", "Cancelar venda"), ("ESTORNAR", "Estornar valor"), ("TROCAR", "Trocar mercadoria")], max_length=20)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("expira_em", models.DateTimeField()),
                ("usado_em", models.DateTimeField(blank=True, null=True)),
                ("revogado_em", models.DateTimeField(blank=True, null=True)),
                ("criado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tokens_criados", to=settings.AUTH_USER_MODEL)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tokens", to="core.empresa")),
                ("usado_por", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="tokens_consumidos", to=settings.AUTH_USER_MODEL)),
                ("venda", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to="core.venda")),
            ],
        ),
        migrations.AddConstraint(model_name="membroempresa", constraint=models.UniqueConstraint(fields=("empresa", "usuario"), name="uniq_usuario_empresa")),
        migrations.AddConstraint(model_name="categoria", constraint=models.UniqueConstraint(fields=("empresa", "nome"), name="uniq_categoria_empresa")),
        migrations.AddConstraint(model_name="produto", constraint=models.UniqueConstraint(fields=("empresa", "codigo"), name="uniq_codigo_produto_empresa")),
        migrations.AddIndex(model_name="produto", index=models.Index(fields=["empresa", "nome"], name="core_produt_empresa_07")),
        migrations.AddIndex(model_name="produto", index=models.Index(fields=["empresa", "codigo_barras"], name="core_produt_empresa_08")),
        migrations.AddConstraint(model_name="venda", constraint=models.UniqueConstraint(fields=("empresa", "idempotency_key"), name="uniq_idempotency_empresa")),
    ]
