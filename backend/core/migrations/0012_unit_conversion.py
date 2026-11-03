from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0011_delivery_request")]

    operations = [
        migrations.AddField(
            model_name="produto", name="unidade_estoque",
            field=models.CharField(blank=True, default="", max_length=12),
        ),
        migrations.AddField(
            model_name="produto", name="fator_conversao",
            field=models.DecimalField(decimal_places=6, default=1, max_digits=12),
        ),
        migrations.AddField(
            model_name="itemvenda", name="fator_conversao",
            field=models.DecimalField(decimal_places=6, default=1, max_digits=12),
        ),
        migrations.AddField(
            model_name="itemcompraentrada", name="fator_conversao",
            field=models.DecimalField(decimal_places=6, default=1, max_digits=12),
        ),
    ]
