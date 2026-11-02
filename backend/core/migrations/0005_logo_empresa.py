from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0004_pedidos_web")]

    operations = [
        migrations.AddField(
            model_name="empresa",
            name="logo",
            field=models.ImageField(blank=True, upload_to="empresas/logos/"),
        ),
    ]
