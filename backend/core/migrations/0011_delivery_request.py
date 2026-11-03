from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0010_accounts_payable")]

    operations = [
        migrations.AddField(
            model_name="pedidoweb",
            name="endereco_entrega",
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name="pedidoweb",
            name="entrega_solicitada_em",
            field=models.DateField(blank=True, null=True),
        ),
    ]
