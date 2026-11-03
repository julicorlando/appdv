from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_cash_closing_totals"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Cliente",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=180)),
                ("documento", models.CharField(blank=True, max_length=14)),
                ("telefone", models.CharField(blank=True, max_length=30)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("endereco", models.CharField(blank=True, max_length=250)),
                ("limite_credito", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("ativo", models.BooleanField(default=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="clientes", to="core.empresa")),
            ],
            options={"ordering": ("nome",)},
        ),
        migrations.AddConstraint(
            model_name="cliente",
            constraint=models.UniqueConstraint(condition=~models.Q(documento=""), fields=("empresa", "documento"), name="uniq_documento_cliente_empresa"),
        ),
        migrations.AddField(
            model_name="venda",
            name="cliente",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="vendas", to="core.cliente"),
        ),
        migrations.CreateModel(
            name="ContaReceber",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("valor_original", models.DecimalField(decimal_places=2, max_digits=12)),
                ("saldo", models.DecimalField(decimal_places=2, max_digits=12)),
                ("vencimento", models.DateField(db_index=True)),
                ("estado", models.CharField(choices=[("ABERTA", "Em aberto"), ("PARCIAL", "Parcialmente paga"), ("QUITADA", "Quitada"), ("CANCELADA", "Cancelada")], db_index=True, default="ABERTA", max_length=12)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("cliente", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="contas_receber", to="core.cliente")),
                ("criado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="contas_receber", to="core.empresa")),
                ("venda", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="conta_receber", to="core.venda")),
            ],
            options={"ordering": ("vencimento", "criado_em")},
        ),
        migrations.CreateModel(
            name="RecebimentoConta",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("meio", models.CharField(choices=[("DINHEIRO", "Dinheiro"), ("CARTAO", "Cartão"), ("PIX", "PIX"), ("OUTRO", "Outro")], max_length=10)),
                ("valor", models.DecimalField(decimal_places=2, max_digits=12)),
                ("referencia", models.CharField(blank=True, max_length=120)),
                ("recebido_em", models.DateTimeField(auto_now_add=True)),
                ("conta", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recebimentos", to="core.contareceber")),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recebimentos_contas", to="core.empresa")),
                ("operador", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-recebido_em",)},
        ),
    ]
