from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0013_stock_loss")]

    operations = [
        migrations.AddField(
            model_name=model_name,
            name=field_name,
            field=models.DecimalField(
                max_digits=12, decimal_places=2, default=0,
                validators=[MinValueValidator(0)],
            ),
        )
        for model_name, field_name in [
            ("empresa", "frete_padrao"), ("pedidoweb", "frete"), ("venda", "frete"),
        ]
    ]
