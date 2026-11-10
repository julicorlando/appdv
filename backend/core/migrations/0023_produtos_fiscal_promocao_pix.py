import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_caixa_em_transacoes_e_recebimentos"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='empresa',
            name='focus_token',
            field=models.CharField("Token Focus NFe", max_length=200, blank=True),
        ),
        migrations.AddField(
            model_name='empresa',
            name='nfce_automatica',
            field=models.BooleanField("Emitir NFC-e automaticamente após cada venda", default=False),
        ),
        migrations.AddField(
            model_name='empresa',
            name='pis_cofins_cst',
            field=models.CharField(
        "CST de PIS/COFINS nas notas", max_length=2, blank=True,
        choices=[("", "— definir com o contador —"), ("01", "01 · Tributável alíquota básica"),
                 ("04", "04 · Monofásica (alíquota zero)"), ("06", "06 · Alíquota zero"), ("07", "07 · Isenta"),
                 ("08", "08 · Sem incidência"), ("49", "49 · Outras operações de saída"), ("99", "99 · Outras operações")],
    ),
        ),
        migrations.AddField(
            model_name='empresa',
            name='mp_access_token',
            field=models.CharField("Access token do Mercado Pago (PIX)", max_length=300, blank=True),
        ),
        migrations.AddField(
            model_name='produto',
            name='foto',
            field=models.ImageField(upload_to="produtos/fotos/", blank=True),
        ),
        migrations.AddField(
            model_name='produto',
            name='preco_promocional',
            field=models.DecimalField("Preço promocional", max_digits=12, decimal_places=2, null=True, blank=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name='produto',
            name='promocao_inicio',
            field=models.DateField("Promoção a partir de", null=True, blank=True),
        ),
        migrations.AddField(
            model_name='produto',
            name='promocao_fim',
            field=models.DateField("Promoção até", null=True, blank=True),
        ),
        migrations.AddField(
            model_name='produto',
            name='ncm',
            field=models.CharField("NCM", max_length=8, blank=True),
        ),
        migrations.AddField(
            model_name='produto',
            name='cest',
            field=models.CharField("CEST", max_length=7, blank=True),
        ),
        migrations.AddField(
            model_name='produto',
            name='cfop',
            field=models.CharField("CFOP", max_length=4, default="5102"),
        ),
        migrations.AddField(
            model_name='produto',
            name='csosn',
            field=models.CharField("CSOSN/CST do ICMS", max_length=3, default="102"),
        ),
        migrations.AddField(
            model_name='produto',
            name='origem',
            field=models.CharField("Origem da mercadoria", max_length=1, default="0", choices=[
        ("0", "0 · Nacional"), ("1", "1 · Estrangeira, importação direta"), ("2", "2 · Estrangeira, mercado interno"),
        ("3", "3 · Nacional, conteúdo importado > 40%"), ("4", "4 · Nacional, processos produtivos básicos"),
        ("5", "5 · Nacional, conteúdo importado ≤ 40%"), ("6", "6 · Estrangeira sem similar, importação direta"),
        ("7", "7 · Estrangeira sem similar, mercado interno"), ("8", "8 · Nacional, conteúdo importado > 70%")]),
        ),
        migrations.AddField(
            model_name='contapagar',
            name='categoria',
            field=models.CharField(max_length=40, blank=True, choices=[
        ("", "Compra de mercadoria"), ("ALUGUEL", "Aluguel"), ("ENERGIA", "Energia, água e internet"),
        ("SALARIOS", "Salários e encargos"), ("IMPOSTOS", "Impostos e taxas"), ("SERVICOS", "Serviços"),
        ("MANUTENCAO", "Manutenção"), ("OUTROS", "Outras despesas")]),
        ),
        migrations.AddField(
            model_name='documentofiscal',
            name='referencia',
            field=models.CharField(max_length=60, blank=True, db_index=True),
        ),
        migrations.AddField(
            model_name='documentofiscal',
            name='ambiente',
            field=models.CharField(max_length=12, blank=True),
        ),
        migrations.AddField(
            model_name='documentofiscal',
            name='numero',
            field=models.CharField(max_length=20, blank=True),
        ),
        migrations.AddField(
            model_name='documentofiscal',
            name='serie',
            field=models.CharField(max_length=5, blank=True),
        ),
        migrations.AddField(
            model_name='documentofiscal',
            name='danfe_url',
            field=models.URLField(max_length=500, blank=True),
        ),
        migrations.AddField(
            model_name='documentofiscal',
            name='xml_url',
            field=models.URLField(max_length=500, blank=True),
        ),
        migrations.AlterField(
            model_name='documentofiscal',
            name='estado',
            field=models.CharField(max_length=16, default='CONFIGURAR', choices=[('CONFIGURAR','Configuração pendente'),('PENDENTE','Aguardando integração'),('PROCESSANDO','Processando na SEFAZ'),('AUTORIZADO','Autorizado'),('REJEITADO','Rejeitado'),('CANCELADO','Cancelado')]),
        ),
        migrations.AddField(
            model_name='terminalpdv',
            name='versao',
            field=models.CharField(max_length=20, blank=True),
        ),
        migrations.CreateModel(
            name='CobrancaPix',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('chave', models.UUIDField(unique=True)),
                ('provedor_id', models.CharField(blank=True, db_index=True, max_length=40)),
                ('valor', models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(0)])),
                ('estado', models.CharField(choices=[('PENDENTE', 'Aguardando pagamento'), ('APROVADO', 'Pago'), ('EXPIRADO', 'Expirado'), ('CANCELADO', 'Cancelado')], db_index=True, default='PENDENTE', max_length=10)),
                ('qr_code', models.TextField(blank=True)),
                ('qr_base64', models.TextField(blank=True)),
                ('criada_em', models.DateTimeField(auto_now_add=True)),
                ('atualizada_em', models.DateTimeField(auto_now=True)),
                ('expira_em', models.DateTimeField()),
                ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cobrancas_pix', to='core.empresa')),
                ('operador', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ('venda', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='cobrancas_pix', to='core.venda')),
            ],
            options={
                'ordering': ('-criada_em',),
            },
        ),
    ]
