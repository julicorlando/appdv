import django.db.models.deletion
from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def preencher_caixa_das_transacoes(apps, schema_editor):
    """Transações antigas continuam no caixa da venda, como eram apuradas antes."""
    Transacao = apps.get_model("core", "Transacao")
    Venda = apps.get_model("core", "Venda")
    Transacao.objects.filter(caixa__isnull=True).update(
        caixa=Subquery(Venda.objects.filter(pk=OuterRef("venda_id")).values("caixa_id")[:1])
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_sessaodesktopticket"),
    ]

    operations = [
        migrations.AddField(
            model_name="transacao",
            name="caixa",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="transacoes", to="core.caixa"),
        ),
        migrations.AddField(
            model_name="recebimentoconta",
            name="caixa",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="recebimentos_conta", to="core.caixa"),
        ),
        migrations.RunPython(preencher_caixa_das_transacoes, migrations.RunPython.noop),
    ]
