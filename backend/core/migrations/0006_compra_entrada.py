from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_logo_empresa"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Fornecedor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=180)),
                ("cnpj", models.CharField(blank=True, max_length=14)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("telefone", models.CharField(blank=True, max_length=30)),
                ("ativo", models.BooleanField(default=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fornecedores", to="core.empresa")),
            ],
            options={"ordering": ("nome",)},
        ),
        migrations.AddConstraint(
            model_name="fornecedor",
            constraint=models.UniqueConstraint(condition=~models.Q(cnpj=""), fields=("empresa", "cnpj"), name="uniq_cnpj_fornecedor_empresa"),
        ),
        migrations.CreateModel(
            name="CompraEntrada",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("numero_nf", models.CharField(max_length=20)),
                ("serie", models.CharField(blank=True, max_length=10)),
                ("chave_acesso", models.CharField(blank=True, max_length=44)),
                ("emitente_cnpj", models.CharField(blank=True, max_length=14)),
                ("data_emissao", models.DateField(blank=True, null=True)),
                ("valor_total", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("estado", models.CharField(choices=[("RASCUNHO", "Aguardando conferência"), ("RECEBIDA", "Recebida"), ("CANCELADA", "Cancelada")], db_index=True, default="RASCUNHO", max_length=12)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("recebido_em", models.DateTimeField(blank=True, null=True)),
                ("criado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="compras_importadas", to=settings.AUTH_USER_MODEL)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="compras_entrada", to="core.empresa")),
                ("fornecedor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="compras", to="core.fornecedor")),
                ("recebido_por", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="compras_recebidas", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-criado_em",)},
        ),
        migrations.AddConstraint(
            model_name="compraentrada",
            constraint=models.UniqueConstraint(fields=("empresa", "emitente_cnpj", "serie", "numero_nf"), name="uniq_nf_entrada_empresa_emitente"),
        ),
        migrations.AddConstraint(
            model_name="compraentrada",
            constraint=models.UniqueConstraint(condition=~models.Q(chave_acesso=""), fields=("empresa", "chave_acesso"), name="uniq_chave_nf_entrada_empresa"),
        ),
        migrations.CreateModel(
            name="ItemCompraEntrada",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo_xml", models.CharField(blank=True, max_length=60)),
                ("descricao_xml", models.CharField(max_length=240)),
                ("unidade_xml", models.CharField(blank=True, max_length=20)),
                ("quantidade", models.DecimalField(decimal_places=3, max_digits=14)),
                ("custo_unitario", models.DecimalField(decimal_places=4, max_digits=14)),
                ("total", models.DecimalField(decimal_places=2, max_digits=14)),
                ("compra", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="itens", to="core.compraentrada")),
                ("produto", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="itens_compra", to="core.produto")),
            ],
        ),
    ]
