import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_inventory_location"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PedidoWeb",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo_publico", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("cliente_nome", models.CharField(max_length=160)),
                ("cliente_telefone", models.CharField(max_length=20)),
                ("estado", models.CharField(choices=[("AGUARDANDO_CLIENTE", "Aguardando cliente"), ("CONFIRMADO", "Confirmado pelo cliente"), ("SEPARANDO", "Em separação"), ("CONCLUIDO", "Venda finalizada"), ("CANCELADO", "Cancelado")], db_index=True, default="AGUARDANDO_CLIENTE", max_length=20)),
                ("subtotal", models.DecimalField(decimal_places=2, max_digits=12)),
                ("desconto", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("total", models.DecimalField(decimal_places=2, max_digits=12)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("expira_em", models.DateTimeField(db_index=True)),
                ("confirmado_em", models.DateTimeField(blank=True, null=True)),
                ("observacao", models.TextField(blank=True)),
                ("criado_por", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="pedidos_web_criados", to=settings.AUTH_USER_MODEL)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pedidos_web", to="core.empresa")),
                ("venda", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="pedido_web", to="core.venda")),
            ],
            options={
                "ordering": ("-criado_em",),
                "indexes": [models.Index(fields=["empresa", "estado", "criado_em"], name="pedido_tenant_status_idx")],
            },
        ),
        migrations.CreateModel(
            name="ItemPedidoWeb",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome_produto", models.CharField(max_length=200)),
                ("codigo_produto", models.CharField(max_length=50)),
                ("quantidade", models.DecimalField(decimal_places=3, max_digits=14)),
                ("preco_unitario", models.DecimalField(decimal_places=2, max_digits=12)),
                ("total", models.DecimalField(decimal_places=2, max_digits=12)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="itens_pedidos_web", to="core.empresa")),
                ("pedido", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="itens", to="core.pedidoweb")),
                ("produto", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="itens_pedidos_web", to="core.produto")),
            ],
        ),
    ]
