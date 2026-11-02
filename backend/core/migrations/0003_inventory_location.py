from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_movimentacaoestoque"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name="produto", name="setor",
            field=models.CharField(blank=True, db_index=True, max_length=80),
        ),
        migrations.AddField(
            model_name="produto", name="endereco",
            field=models.CharField(blank=True, db_index=True, max_length=100),
        ),
        migrations.CreateModel(
            name="InventarioContagem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("setor", models.CharField(max_length=80)),
                ("data_programada", models.DateField(db_index=True)),
                ("quantidade_itens", models.PositiveIntegerField(default=20)),
                ("estado", models.CharField(choices=[("PROGRAMADO", "Programado"), ("ABERTO", "Em contagem"), ("CONCLUIDO", "Concluído")], default="PROGRAMADO", max_length=12)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("iniciado_em", models.DateTimeField(blank=True, null=True)),
                ("concluido_em", models.DateTimeField(blank=True, null=True)),
                ("criado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="contagens_criadas", to=settings.AUTH_USER_MODEL)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="contagens", to="core.empresa")),
            ],
            options={"ordering": ("data_programada", "setor")},
        ),
        migrations.CreateModel(
            name="ItemContagem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("saldo_sistema", models.DecimalField(decimal_places=3, max_digits=14)),
                ("quantidade_contada", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("diferenca", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("contado_em", models.DateTimeField(blank=True, null=True)),
                ("contado_por", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("contagem", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="itens", to="core.inventariocontagem")),
                ("produto", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="contagens", to="core.produto")),
            ],
        ),
        migrations.AddConstraint(
            model_name="inventariocontagem",
            constraint=models.UniqueConstraint(fields=("empresa", "data_programada", "setor"), name="uniq_contagem_dia_setor_empresa"),
        ),
        migrations.AddConstraint(
            model_name="itemcontagem",
            constraint=models.UniqueConstraint(fields=("contagem", "produto"), name="uniq_produto_contagem"),
        ),
    ]
