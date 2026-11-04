from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0014_frete")]

    operations = [
        migrations.AddField("inventariocontagem", "em_recontagem", models.BooleanField(default=False)),
        migrations.AddField("inventariocontagem", "revisada_em", models.DateTimeField(null=True, blank=True)),
        migrations.AddField("itemcontagem", "primeira_quantidade", models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)),
    ]
