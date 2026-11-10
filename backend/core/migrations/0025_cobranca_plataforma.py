import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def assinaturas_existentes(apps, schema_editor):
    """Empresas anteriores à cobrança ficam isentas até o master escolher o plano (nada é bloqueado de surpresa)."""
    Empresa = apps.get_model("core", "Empresa")
    Assinatura = apps.get_model("core", "Assinatura")
    for empresa in Empresa.objects.all():
        Assinatura.objects.get_or_create(empresa=empresa, defaults={
            "estado": "ISENTA", "isenta": True,
            "motivo_isencao": "Cliente anterior à cobrança — defina o plano no Painel Master",
        })


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_movimentacaocaixa_chave"),
    ]

    operations = [
        migrations.CreateModel(
            name="Plano",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=80)),
                ("descricao", models.CharField(blank=True, max_length=250, verbose_name="Descrição")),
                ("valor_mensal", models.DecimalField(decimal_places=2, max_digits=10, validators=[django.core.validators.MinValueValidator(0)], verbose_name="Valor mensal (R$)")),
                ("caixas_incluidos", models.PositiveIntegerField(default=1, verbose_name="Caixas (PDV) incluídos")),
                ("valor_caixa_adicional", models.DecimalField(decimal_places=2, default=0, max_digits=10, validators=[django.core.validators.MinValueValidator(0)], verbose_name="Valor por caixa adicional (R$)")),
                ("limite_usuarios", models.PositiveIntegerField(default=0, verbose_name="Limite de usuários (0 = sem limite)")),
                ("ativo", models.BooleanField(default=True)),
                ("ordem", models.PositiveIntegerField(default=0)),
            ],
            options={"ordering": ("ordem", "valor_mensal")},
        ),
        migrations.CreateModel(
            name="ConfiguracaoMaster",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mp_access_token", models.CharField(blank=True, max_length=300, verbose_name="Access token do Mercado Pago (recebimento do master)")),
                ("dias_teste", models.PositiveIntegerField(default=15, verbose_name="Dias de teste grátis para empresas novas")),
                ("carencia_dias", models.PositiveIntegerField(default=5, verbose_name="Dias de carência após o vencimento")),
                ("dias_antecedencia", models.PositiveIntegerField(default=10, verbose_name="Gerar a fatura quantos dias antes do vencimento")),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("plano_padrao", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="core.plano", verbose_name="Plano padrão de empresas novas")),
            ],
        ),
        migrations.CreateModel(
            name="Assinatura",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("estado", models.CharField(choices=[("TESTE", "Teste grátis"), ("ATIVA", "Em dia"), ("ATRASADA", "Fatura vencida (em carência)"), ("SUSPENSA", "Suspensa por falta de pagamento"), ("ISENTA", "Isenta"), ("CANCELADA", "Cancelada")], db_index=True, default="TESTE", max_length=10)),
                ("teste_ate", models.DateField(blank=True, null=True, verbose_name="Teste grátis até")),
                ("isenta", models.BooleanField(default=False, verbose_name="Isenta de cobrança")),
                ("isencao_ate", models.DateField(blank=True, null=True, verbose_name="Isenta até (em branco = sem prazo)")),
                ("motivo_isencao", models.CharField(blank=True, max_length=200, verbose_name="Motivo da isenção")),
                ("dia_vencimento", models.PositiveSmallIntegerField(default=10, verbose_name="Dia do vencimento")),
                ("carencia_dias", models.PositiveIntegerField(blank=True, help_text="Em branco usa o padrão da plataforma.", null=True, verbose_name="Carência (dias)")),
                ("valor_especial", models.DecimalField(blank=True, decimal_places=2, help_text="Substitui o valor do plano (caixas adicionais continuam somando).", max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)], verbose_name="Valor mensal especial (R$)")),
                ("desconto_percentual", models.DecimalField(decimal_places=2, default=0, max_digits=5, validators=[django.core.validators.MinValueValidator(0)], verbose_name="Desconto (%)")),
                ("observacao", models.CharField(blank=True, max_length=250, verbose_name="Observação interna")),
                ("suspensa_em", models.DateField(blank=True, null=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("empresa", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="assinatura", to="core.empresa")),
                ("plano", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="core.plano")),
            ],
        ),
        migrations.CreateModel(
            name="Fatura",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("competencia", models.DateField(help_text="Primeiro dia do mês cobrado.")),
                ("descricao", models.CharField(max_length=200)),
                ("itens", models.JSONField(default=list)),
                ("valor", models.DecimalField(decimal_places=2, max_digits=10)),
                ("vencimento", models.DateField()),
                ("estado", models.CharField(choices=[("ABERTA", "Em aberto"), ("VENCIDA", "Vencida"), ("PAGA", "Paga"), ("CANCELADA", "Cancelada")], db_index=True, default="ABERTA", max_length=10)),
                ("paga_em", models.DateTimeField(blank=True, null=True)),
                ("meio", models.CharField(blank=True, max_length=20)),
                ("mp_preference_id", models.CharField(blank=True, max_length=80)),
                ("mp_payment_id", models.CharField(blank=True, db_index=True, max_length=40)),
                ("link_pagamento", models.URLField(blank=True, max_length=500)),
                ("observacao", models.CharField(blank=True, max_length=250)),
                ("criada_em", models.DateTimeField(auto_now_add=True)),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="faturas", to="core.empresa")),
            ],
            options={"ordering": ("-vencimento", "-pk")},
        ),
        migrations.AddConstraint(
            model_name="fatura",
            constraint=models.UniqueConstraint(fields=("empresa", "competencia"), name="fatura_unica_por_competencia"),
        ),
        migrations.RunPython(assinaturas_existentes, migrations.RunPython.noop),
    ]
