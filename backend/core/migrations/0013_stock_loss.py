from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0012_unit_conversion")]

    operations = [
        migrations.AlterField(
            model_name="movimentacaoestoque",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("ENTRADA", "Entrada"), ("VENDA", "Venda"),
                    ("DEVOLUCAO", "Devolução"), ("PERDA", "Perda"),
                    ("AJUSTE", "Ajuste"),
                ],
                max_length=12,
            ),
        ),
    ]
