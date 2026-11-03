from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_clients_receivables"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="compraentrada",
            name="vencimento",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name="ContaPagar",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("descricao", models.CharField(max_length=240)),
                ("valor_original", models.DecimalField(decimal_places=2, max_digits=14)),
                ("saldo", models.DecimalField(decimal_places=2, max_digits=14)),
                ("vencimento", models.DateField(db_index=True)),
                ("estado", models.CharField(choices=[("ABERTA", "Em aberto"), ("PARCIAL", "Parcialmente paga"), ("QUITADA", "Quitada"), ("CANCELADA", "Cancelada")], db_index=True, default="ABERTA", max_length=12)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("compra", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="conta_pagar", to="core.compraentrada")),
                ("criado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="contas_pagar_criadas", to=settings.AUTH_USER_MODEL)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="contas_pagar", to="core.empresa")),
                ("fornecedor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="contas_pagar", to="core.fornecedor")),
            ],
            options={"ordering": ("vencimento", "criado_em")},
        ),
        migrations.CreateModel(
            name="PagamentoContaPagar",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("meio", models.CharField(choices=[("DINHEIRO", "Dinheiro"), ("CARTAO", "Cartão"), ("PIX", "PIX"), ("OUTRO", "Outro")], max_length=10)),
                ("valor", models.DecimalField(decimal_places=2, max_digits=14)),
                ("referencia", models.CharField(blank=True, max_length=120)),
                ("pago_em", models.DateTimeField(auto_now_add=True)),
                ("conta", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="pagamentos", to="core.contapagar")),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pagamentos_contas_pagar", to="core.empresa")),
                ("operador", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-pago_em",)},
        ),
    ]
