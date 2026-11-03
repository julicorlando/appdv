from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def preencher_valor_recebido(apps, schema_editor):
    Transacao = apps.get_model("core", "Transacao")
    Transacao.objects.filter(valor_recebido=0, tipo="PAGAMENTO").update(
        valor_recebido=models.F("valor")
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_compra_entrada"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="transacao",
            name="valor_recebido",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="transacao",
            name="troco",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.RunPython(preencher_valor_recebido, migrations.RunPython.noop),
        migrations.CreateModel(
            name="MovimentacaoCaixa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo", models.CharField(choices=[("SANGRIA", "Sangria"), ("SUPRIMENTO", "Suprimento")], max_length=12)),
                ("valor", models.DecimalField(decimal_places=2, max_digits=12)),
                ("motivo", models.CharField(max_length=250)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("caixa", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="movimentacoes", to="core.caixa")),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="movimentacoes_caixa", to="core.empresa")),
                ("operador", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-criado_em",)},
        ),
    ]
