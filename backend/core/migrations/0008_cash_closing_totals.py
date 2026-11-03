from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0007_cash_control")]

    operations = [
        migrations.AddField(
            model_name="caixa",
            name="saldo_esperado_fechamento",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="caixa",
            name="diferenca_fechamento",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
    ]
