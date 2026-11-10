from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_produtos_fiscal_promocao_pix"),
    ]

    operations = [
        migrations.AddField(
            model_name="movimentacaocaixa",
            name="chave",
            field=models.UUIDField(blank=True, null=True, unique=True),
        ),
    ]
