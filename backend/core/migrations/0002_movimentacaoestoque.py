from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="MovimentacaoEstoque",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo", models.CharField(choices=[("ENTRADA", "Entrada"), ("VENDA", "Venda"), ("DEVOLUCAO", "Devolução"), ("AJUSTE", "Ajuste")], max_length=12)),
                ("quantidade", models.DecimalField(decimal_places=3, max_digits=14)),
                ("referencia", models.CharField(blank=True, max_length=120)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="movimentacoes_estoque", to="core.empresa")),
                ("operador", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("produto", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="movimentacoes", to="core.produto")),
            ],
            options={"ordering": ("-criado_em",)},
        ),
    ]
