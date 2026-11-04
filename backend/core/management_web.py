import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q, Sum, F
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from .models import *
from .web import require_membership
from .management_forms import *
from .management_services import *
from .orcamentos import salvar_orcamento, dados_orcamento
from .discount_policy import fingerprint


def member(request,empresa_id):
    return require_membership(request,empresa_id,('ADMIN','GERENTE'))


def screen(request,m,title,headers=(),rows=(),form=None,**context):
    return render(request,'gestao.html',dict(empresa=m.empresa,membro=m,title=title,headers=headers,rows=rows,form=form,**context))


def money(value):
    return f'R$ {value:.2f}'


@login_required
def cliente_editar(request,empresa_id,pk):
    m = member(request,empresa_id)
    obj = get_object_or_404(Cliente,empresa=m.empresa,pk=pk)
    form = ClienteEditForm(request.POST or None,instance=obj,empresa=m.empresa)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            form.save()
            audit(m.empresa,request.user,'CLIENTE_EDITADO',obj.pk,campos=form.changed_data)
        messages.success(request,'Cadastro do cliente atualizado. Vendas e histórico foram preservados.')
        return redirect('empresa-clientes',empresa_id=empresa_id)
    saldo = CreditoLoja.objects.filter(empresa=m.empresa,cliente=obj).aggregate(n=Sum('valor'))['n'] or ZERO
    return screen(request,m,'Editar cliente',form=form,note=f'Crédito em loja: {money(saldo)}. Alterar o cadastro não modifica documentos já emitidos.')


@login_required
def orcamentos(request,empresa_id):
    m = require_membership(request,empresa_id)
    qs = Orcamento.objects.filter(empresa=m.empresa).select_related('cliente','operador')
    q = request.GET.get('q','').strip()
    if q:
        qs = qs.filter(Q(cliente__nome__icontains=q)|Q(cliente__documento__icontains=''.join(c for c in q if c.isdigit()) or q)|Q(pk=int(q) if q.isdigit() else -1))
    estado = request.GET.get('estado','')
    if estado == 'ABERTO': qs = qs.filter(venda__isnull=True,cancelado=False,validade__gte=timezone.localdate())
    elif estado == 'VENCIDO': qs = qs.filter(venda__isnull=True,cancelado=False,validade__lt=timezone.localdate())
    elif estado == 'CONVERTIDO': qs = qs.filter(venda__isnull=False)
    rows = [dict(cells=[o.pk,o.cliente.nome,o.cliente.documento,o.operador.username,o.validade,dados_orcamento(o)['estado'],money(o.total)],url=reverse('gestao-orcamento',args=[empresa_id,o.pk])) for o in qs.order_by('-pk')[:200]]
    return screen(request,m,'Central de orçamentos',['Número','Cliente','CPF/CNPJ','Vendedor','Validade','Estado','Total'],rows,search=True,new_url=reverse('gestao-orcamento-novo',args=[empresa_id]))


@login_required
def orcamento_editar(request,empresa_id,pk=None):
    m = require_membership(request,empresa_id)
    obj = get_object_or_404(Orcamento,pk=pk,empresa=m.empresa) if pk else None
    initial = dict(cliente=obj.cliente_id,validade=obj.validade,chave=uuid.uuid4()) if obj else dict(validade=timezone.localdate()+timedelta(days=7),chave=uuid.uuid4())
    form = QuoteForm(request.POST or None,initial=initial,empresa=m.empresa)
    initial_lines = [dict(produto=i['produto'],quantidade=i['quantidade'],desconto=i['desconto']) for i in obj.itens] if obj else []
    lines = QuoteLines(request.POST or None,initial=initial_lines,form_kwargs={'empresa':m.empresa})
    if request.method == 'POST' and form.is_valid() and lines.is_valid():
        payload = {'cliente':form.cleaned_data['cliente'].pk,'validade':str(form.cleaned_data['validade']),'chave':str(form.cleaned_data['chave']), 'itens':[dict(produto=i['produto'].pk,quantidade=str(i['quantidade']),desconto=str(i['desconto'])) for i in lines.cleaned_data if i and not i.get('DELETE')]}
        try:
            with transaction.atomic():
                if form.cleaned_data['supervisor'] or form.cleaned_data['senha']:
                    supervisor = authenticate(request,username=form.cleaned_data['supervisor'],password=form.cleaned_data['senha'])
                    if not supervisor or not MembroEmpresa.objects.filter(empresa=m.empresa,usuario=supervisor,ativo=True,papel__in=['ADMIN','GERENTE']).exists():
                        raise ValueError('Autorização inválida nesta empresa.')
                    approval = AprovacaoDesconto.objects.create(empresa=m.empresa,operador=request.user,aprovador=supervisor,chave_operacao=payload['chave'],resumo_itens=fingerprint(payload['itens']))
                    payload['autorizacao_desconto'] = str(approval.codigo)
                saved,created = salvar_orcamento(m.empresa,m,payload)
                audit(m.empresa,request.user,'ORCAMENTO_SALVO',saved.pk,origem=obj.pk if obj else None)
            return redirect('gestao-orcamento',empresa_id=empresa_id,pk=saved.pk)
        except Exception as exc:
            form.add_error(None,str(exc))
    return screen(request,m,'Duplicar / revisar orçamento' if obj else 'Novo orçamento',form=form,lines=lines,note='Uma revisão cria novo número e preserva o orçamento original. Preços são calculados pelo servidor.')


@login_required
def orcamento(request,empresa_id,pk):
    m = require_membership(request,empresa_id)
    obj = get_object_or_404(Orcamento.objects.select_related('cliente','empresa'),empresa=m.empresa,pk=pk)
    if request.method == 'POST':
        try:
            if request.POST.get('acao') == 'reservar':
                reservar_orcamento(m.empresa,request.user,obj.pk,request.POST.get('deposito'),request.POST.get('endereco',''),request.POST.get('prevista') or None,request.POST.get('responsavel',''))
                messages.success(request,'Pedido criado e reserva registrada. Finalize a venda antes de entregar.')
                return redirect('gestao-entregas',empresa_id=empresa_id)
            elif request.POST.get('acao') == 'cancelar':
                if m.papel == 'CAIXA': raise PermissionDenied()
                with transaction.atomic():
                    obj = Orcamento.objects.select_for_update().get(pk=obj.pk,empresa=m.empresa)
                    if obj.venda_id: raise ValueError('Orçamento já convertido.')
                    order = OrdemEntrega.objects.select_for_update().filter(orcamento=obj).first()
                    if order: order.estado='CANCELADO'; order.save(update_fields=['estado'])
                    obj.cancelado=True;obj.save(update_fields=['cancelado'])
                    audit(m.empresa,request.user,'ORCAMENTO_CANCELADO',obj.pk)
            elif request.POST.get('acao') == 'vender':

                payments = [{'meio':meio,'valor':str(Decimal(request.POST.get(meio,'0') or '0'))} for meio in ['DINHEIRO','PIX','CARTAO','OUTRO','LOJA'] if Decimal(request.POST.get(meio,'0') or '0') > 0]
                cash = Caixa.objects.filter(empresa=m.empresa,operador=request.user,estado='ABERTO').first()
                if not cash: raise ValueError('Abra seu Caixa Web antes de converter o orçamento.')
                payload = dict(idempotency_key=str(request.POST.get('chave')),caixa=cash.pk,cliente=obj.cliente_id,orcamento=obj.pk,total_confirmado=request.POST.get('total_confirmado'),itens=[dict(produto=i['produto'],quantidade=i['quantidade'],desconto=i['desconto']) for i in obj.itens],pagamentos=payments)
                if request.POST.get('supervisor'):
                    supervisor=authenticate(request,username=request.POST['supervisor'],password=request.POST.get('senha',''))
                    if not supervisor or not MembroEmpresa.objects.filter(empresa=m.empresa,usuario=supervisor,ativo=True,papel__in=['ADMIN','GERENTE']).exists(): raise ValueError('Autorização inválida.')
                    a=AprovacaoDesconto.objects.create(empresa=m.empresa,operador=request.user,aprovador=supervisor,chave_operacao=payload['idempotency_key'],resumo_itens=fingerprint(payload['itens']))
                    payload['autorizacao_desconto']=str(a.codigo)
                sale,created=vender_orcamento(m.empresa,request.user,payload)
                messages.success(request,f"Venda #{sale.pk} registrada.")
            else: raise ValueError('Ação inválida.')
        except Exception as exc: messages.error(request,str(exc))
        return redirect('gestao-orcamento',empresa_id=empresa_id,pk=pk)
    current = ZERO
    data=[]
    for line in obj.itens:
        p=Produto.objects.get(pk=line['produto'],empresa=m.empresa)
        total=(p.preco_venda*Decimal(line['quantidade'])).quantize(Decimal('.01'))-Decimal(line['desconto'])
        current+=total
        data.append(dict(line,atual=p.preco_venda,total_atual=total,endereco=p.endereco))
    principal(m.empresa)
    order=OrdemEntrega.objects.filter(orcamento=obj).first()
    frete=order.frete if order else ZERO
    return render(request,'gestao_orcamento.html',dict(empresa=m.empresa,membro=m,obj=obj,itens=data,total_atual=current+frete,frete=frete,depositos=Deposito.objects.filter(empresa=m.empresa),chave=uuid.uuid4(),estado=dados_orcamento(obj)['estado']))


@login_required
def entregas(request,empresa_id):
    m=member(request,empresa_id)
    if request.method=='POST':
        try:
            with transaction.atomic():
                order=OrdemEntrega.objects.select_for_update().get(pk=request.POST.get('ordem'),empresa=m.empresa)
                if request.POST.get('acao')=='entregar':
                    registrar_entrega(m.empresa,request.user,order.pk,{key[4:]:value for key,value in request.POST.items() if key.startswith('qtd_')},request.POST.get('recebido_por',''))
                else:
                    target=request.POST.get('estado')
                    transitions={'AGUARDANDO':['PRONTO','CANCELADO'],'PRONTO':['EM_ROTA','CANCELADO'],'EM_ROTA':['PRONTO'],'PARCIAL':['EM_ROTA']}
                    if target not in transitions.get(order.estado,[]):raise ValueError('Transição inválida. Entregas são concluídas pelas quantidades registradas.')
                    if target=='CANCELADO' and order.venda_id: raise ValueError('Venda paga: trate a devolução antes de cancelar a entrega.')
                    order.estado=target;order.responsavel=request.POST.get('responsavel','');order.save()
                    audit(m.empresa,request.user,'ESTADO_ENTREGA',order.pk,estado=target)
            messages.success(request,'Entrega atualizada.')
        except Exception as exc:messages.error(request,str(exc))
        return redirect('gestao-entregas',empresa_id=empresa_id)
    orders=OrdemEntrega.objects.filter(empresa=m.empresa).select_related('orcamento__cliente').prefetch_related('itens__produto','itens__deposito').order_by('-pk')[:100]
    return render(request,'gestao_entregas.html',dict(empresa=m.empresa,membro=m,orders=orders))


@login_required
def depositos(request,empresa_id):
    m=member(request,empresa_id);principal(m.empresa)
    form=DepositoForm(request.POST or None)
    if request.method=='POST':
        try:
            if request.POST.get('acao')=='transferir':transferir(m.empresa,request.user,request.POST.get('produto'),request.POST.get('origem'),request.POST.get('destino'),request.POST.get('quantidade'))
            elif form.is_valid():
                obj=form.save(commit=False);obj.empresa=m.empresa;obj.save()
            else:raise ValueError(str(form.errors))
            messages.success(request,'Depósito/transferência registrado.')
        except Exception as exc:messages.error(request,str(exc))
        return redirect('gestao-depositos',empresa_id=empresa_id)
    locations=list(Deposito.objects.filter(empresa=m.empresa))
    rows=[]
    for p in Produto.objects.filter(empresa=m.empresa,ativo=True).order_by('nome')[:500]:
        for d in locations:
            balance=saldo_local(p,d);reserved=reservado(p,d)
            rows.append(dict(cells=[p.codigo,p.nome,d.nome,p.unidade_estoque or p.unidade,balance,reserved,balance-reserved]))
    return screen(request,m,'Depósitos e transferências',['Código','Produto','Depósito','Unidade base','Saldo','Reservado','Disponível'],rows,form=form,locations=locations,products=Produto.objects.filter(empresa=m.empresa,ativo=True),transfer=True)


@login_required
def devolucoes(request,empresa_id):
    m=member(request,empresa_id)
    if request.method=='POST':
        try:
            result=devolver(m.empresa,request.user,request.POST.get('item'),request.POST.get('quantidade'),request.POST.get('destino'),request.POST.get('financeiro'),request.POST.get('motivo',''),uuid.UUID(request.POST.get('chave','')),request.POST.get('meio','OUTRO'))
            messages.success(request,f'Devolução #{result.pk}: {money(result.valor)}. Estorno registra a obrigação; confirme a devolução do pagamento com o cliente.')
        except Exception as exc:messages.error(request,str(exc))
        return redirect('gestao-devolucoes',empresa_id=empresa_id)
    sale_id=request.GET.get('venda','')
    items=ItemVenda.objects.filter(empresa=m.empresa,venda__estado='CONCLUIDA').select_related('produto','venda__cliente')
    items=items.filter(venda_id=int(sale_id)) if sale_id.isdigit() else items.none()
    rows=[dict(cells=[r.pk,r.item.venda_id,r.item.produto.nome,r.quantidade,money(r.valor),r.get_destino_display(),r.get_financeiro_display(),r.motivo]) for r in DevolucaoItem.objects.filter(empresa=m.empresa).select_related('item__produto').order_by('-pk')[:100]]
    return screen(request,m,'Trocas e devoluções',['Nº','Venda','Produto','Quantidade','Valor','Estoque','Financeiro','Motivo'],rows,return_items=items,chave=uuid.uuid4(),returns=True)


@login_required
def caixas(request,empresa_id,pk=None):
    from .api import _resumo_caixa
    m=member(request,empresa_id)
    qs=Caixa.objects.filter(empresa=m.empresa).select_related('operador').order_by('-pk')
    if pk:qs=qs.filter(pk=pk)
    rows=[];details=[]
    for c in qs[:100]:
        summary=_resumo_caixa(c)
        rows.append(dict(cells=[c.pk,c.operador.username,c.aberto_em,c.estado,money(c.saldo_abertura),summary['esperado_dinheiro'],c.saldo_informado_fechamento,c.diferenca_fechamento],url=reverse('gestao-caixa',args=[empresa_id,c.pk])))
        if pk:
            details=list(MovimentacaoCaixa.objects.filter(empresa=m.empresa,caixa=c).values('tipo','valor','motivo'))
            details+=list(Transacao.objects.filter(empresa=m.empresa,venda__caixa=c).values('venda_id','tipo','meio','valor','troco'))
    return screen(request,m,'Conferência de caixas',['Caixa','Operador','Abertura','Estado','Fundo','Dinheiro esperado','Contagem','Diferença'],rows,details=details)


@login_required
def sincronizacao(request,empresa_id):
    m=member(request,empresa_id)
    rows=[dict(cells=[t.identificador,t.operador.username,t.ultimo_contato,'Sem contato recente' if t.ultimo_contato<timezone.now()-timedelta(seconds=90) else 'Online',t.pendentes,t.erros]) for t in TerminalPDV.objects.filter(empresa=m.empresa).select_related('operador')]
    return screen(request,m,'Central de sincronização',['Terminal','Operador','Último contato','Conexão','Pendências','Motivos'],rows,note='Informações enviadas pelo aplicativo atualizado. Pendências locais só chegam ao painel quando o terminal consegue se conectar.')


@login_required
def descontos(request,empresa_id):
    m=require_membership(request,empresa_id,('ADMIN',))
    policy,_=PoliticaDesconto.objects.get_or_create(empresa=m.empresa)
    form=PoliticaForm(request.POST or None,instance=policy)
    if request.method=='POST' and form.is_valid():
        form.save();audit(m.empresa,request.user,'POLITICA_DESCONTO',policy.pk,campos=form.changed_data)
        messages.success(request,'Política salva para novas operações. Atualize os caixas para carregar a política.')
        return redirect('gestao-descontos',empresa_id=empresa_id)
    rows=[dict(cells=[a.pk,a.operador.username,a.aprovador.username,a.criada_em,a.venda_id or 'Não utilizada']) for a in AprovacaoDesconto.objects.filter(empresa=m.empresa).select_related('operador','aprovador').order_by('-pk')[:100]]
    return screen(request,m,'Gestão de descontos',['Autorização','Operador','Aprovador','Data','Venda'],rows,form=form,note='Automático: regra aplicada apenas quando a forma de pagamento satisfaz a condição. Descontos manuais acima do limite exigem autorização.')


@login_required
def relatorios(request,empresa_id):
    m=member(request,empresa_id)
    qs=ItemVenda.objects.filter(empresa=m.empresa,venda__estado='CONCLUIDA').select_related('venda__operador','produto__categoria').prefetch_related('devolucoes')
    try:
        for key,lookup in [('inicio','venda__realizada_em__date__gte'),('fim','venda__realizada_em__date__lte')]:
            if request.GET.get(key):qs=qs.filter(**{lookup:date.fromisoformat(request.GET[key])})
        if request.GET.get('vendedor'):qs=qs.filter(venda__operador_id=int(request.GET['vendedor']))
        if request.GET.get('categoria'):qs=qs.filter(produto__categoria_id=int(request.GET['categoria']))
    except ValueError:
        messages.error(request,'Filtros inválidos.');qs=qs.none()
    rows=[]
    for item in qs.order_by('-venda_id')[:1000]:
        returns=list(item.devolucoes.all());rev=item.total-sum((r.valor for r in returns),ZERO)
        q=item.quantidade-sum((r.quantidade for r in returns),ZERO)
        cost=(item.custo_estoque_unitario*q*item.fator_conversao).quantize(Decimal('.01')) if item.custo_estoque_unitario is not None else None
        margin=(rev-cost) if cost is not None else None
        rows.append(dict(cells=[item.venda_id,item.venda.realizada_em,item.venda.operador.username,item.produto.nome,item.produto.categoria.nome,money(rev),money(cost) if cost is not None else 'Sem custo histórico',money(margin) if margin is not None else '—',f'{margin/rev*100:.2f}%' if margin is not None and rev else '—',money(item.desconto)]))
    return screen(request,m,'Margem por venda e produto',['Venda','Data','Vendedor','Produto','Categoria','Receita líquida','Custo','Margem R$','Margem %','Desconto'],rows,filters=True,vendedores=MembroEmpresa.objects.filter(empresa=m.empresa).select_related('usuario'),categorias=Categoria.objects.filter(empresa=m.empresa),note='Receita de produtos líquida de devoluções; frete excluído. Custo é congelado nas novas vendas. Vendas anteriores sem custo histórico aparecem sem margem calculada. Limite de 1.000 linhas por consulta.')


@login_required
def reposicao(request,empresa_id):
    m=member(request,empresa_id)
    if request.method=='POST':
        try:
            with transaction.atomic():
                if request.POST.get('acao')=='receber':
                    confirmar_compra_pedido(m.empresa,request.user,request.POST.get('nota'),request.POST.get('pedido'),request.POST.get('aceitar_custo')=='on')
                elif request.POST.get('acao') in ['editar','cancelar']:
                    order=PedidoCompra.objects.select_for_update().get(pk=request.POST.get('pedido'),empresa=m.empresa)
                    if order.estado not in ['ABERTO','PARCIAL']:raise ValueError('Pedido encerrado.')
                    if request.POST['acao']=='cancelar':
                        order.estado='CANCELADO';order.save(update_fields=['estado'])
                    else:
                        for item in order.itens.select_for_update():
                            q=quantity(request.POST.get(f'qtd_{item.pk}'))
                            cost=Decimal(request.POST.get(f'custo_{item.pk}'))
                            if q<item.recebido or not cost.is_finite() or cost<0:raise ValueError('Quantidade menor que o recebido ou custo inválido.')
                            item.quantidade=q;item.custo=cost;item.save(update_fields=['quantidade','custo'])
                    audit(m.empresa,request.user,'PEDIDO_COMPRA_'+request.POST['acao'].upper(),order.pk)
                else:
                    supplier=Fornecedor.objects.get(pk=request.POST.get('fornecedor'),empresa=m.empresa)
                    order=PedidoCompra.objects.create(empresa=m.empresa,fornecedor=supplier,criado_por=request.user)
                    for key,raw in request.POST.items():
                        if key.startswith('qtd_') and raw and Decimal(raw)>0:
                            p=Produto.objects.get(pk=key[4:],empresa=m.empresa,ativo=True)
                            ItemPedidoCompra.objects.create(pedido=order,produto=p,quantidade=quantity(raw),custo=p.custo_medio*p.fator_conversao)
                    if not order.itens.exists():raise ValueError('Informe ao menos um item para comprar.')
                    audit(m.empresa,request.user,'PEDIDO_COMPRA',order.pk)
            messages.success(request,'Operação de compra registrada.')
        except Exception as exc:messages.error(request,str(exc))
        return redirect('gestao-reposicao',empresa_id=empresa_id)
    suggestions=[]
    for p in Produto.objects.filter(empresa=m.empresa,ativo=True).order_by('nome'):
        reserved=reservado(p)
        incoming=sum(((i.quantidade-i.recebido)*p.fator_conversao for i in ItemPedidoCompra.objects.filter(produto=p,pedido__estado__in=['ABERTO','PARCIAL'])),ZERO)
        need=max(ZERO,p.estoque_minimo+reserved-p.estoque_atual-incoming)
        suggestions.append(dict(produto=p,reservado=reserved,entrada=incoming,sugestao=(need/p.fator_conversao).quantize(Decimal('.001'),rounding=__import__('decimal').ROUND_UP)))
    orders=PedidoCompra.objects.filter(empresa=m.empresa).select_related('fornecedor').prefetch_related('itens__produto').order_by('-pk')[:100]
    return render(request,'gestao_reposicao.html',dict(empresa=m.empresa,membro=m,suggestions=suggestions,orders=orders,fornecedores=Fornecedor.objects.filter(empresa=m.empresa),notas=CompraEntrada.objects.filter(empresa=m.empresa,estado='RASCUNHO').prefetch_related('itens__produto')))


@login_required
def fiscal(request,empresa_id):
    m=member(request,empresa_id)
    if request.method=='POST':
        sale=get_object_or_404(Venda,empresa=m.empresa,pk=request.POST.get('venda'))
        doc,_=DocumentoFiscal.objects.get_or_create(empresa=m.empresa,venda=sale,defaults={'mensagem':'Emissão ainda depende de integração homologada com provedor e certificado.'})
        audit(m.empresa,request.user,'FISCAL_SOLICITADO',doc.pk)
        messages.warning(request,'Solicitação registrada. Nenhuma NF-e foi emitida: falta integrar o provedor e configurar certificado e tributação dos produtos.')
        return redirect('gestao-fiscal',empresa_id=empresa_id)
    rows=[dict(cells=[d.venda_id,d.get_estado_display(),d.chave,d.protocolo,d.mensagem,d.atualizado_em]) for d in DocumentoFiscal.objects.filter(empresa=m.empresa).select_related('venda').order_by('-pk')[:200]]
    return screen(request,m,'Central fiscal',['Venda','Estado','Chave','Protocolo','Retorno','Atualizado'],rows,fiscal=True,sales=Venda.objects.filter(empresa=m.empresa,estado='CONCLUIDA').order_by('-pk')[:200],note='Cadastro fiscal e acompanhamento de solicitações. Emissão, cancelamento na SEFAZ, XML autorizado e DANFE exigem integração fiscal real, ainda não configurada. Nenhuma autorização é simulada.')


@login_required
def equipe(request,empresa_id):
    m=require_membership(request,empresa_id,('ADMIN',))
    if request.method=='POST':
        try:
            from django.contrib.auth import get_user_model
            from django.contrib.auth.password_validation import validate_password
            with transaction.atomic():
                Empresa.objects.select_for_update().get(pk=m.empresa.pk)
                role=request.POST.get('papel','CAIXA')
                if role not in ['CAIXA','GERENTE','ADMIN']:raise ValueError('Perfil inválido.')
                if request.POST.get('membro'):
                    target=MembroEmpresa.objects.select_for_update().get(pk=request.POST['membro'],empresa=m.empresa)
                    active=request.POST.get('ativo')=='on'
                    if target.usuario_id==request.user.pk and (not active or role!='ADMIN'):raise ValueError('Seu próprio acesso administrativo deve ser preservado.')
                    if target.papel=='ADMIN' and (not active or role!='ADMIN') and not MembroEmpresa.objects.filter(empresa=m.empresa,papel='ADMIN',ativo=True).exclude(pk=target.pk).exists():raise ValueError('Mantenha ao menos um administrador ativo.')
                    target.papel=role;target.ativo=active;target.save(update_fields=['papel','ativo'])
                    audit(m.empresa,request.user,'MEMBRO_ATUALIZADO',target.pk,papel=role,ativo=active)
                else:
                    username=request.POST.get('username','').strip()
                    password=request.POST.get('senha','')
                    if not username or len(username)>150:raise ValueError('Informe um usuário válido.')
                    user=get_user_model()(username=username,email=request.POST.get('email',''))
                    validate_password(password,user)
                    if len(password)<10:raise ValueError('A senha inicial deve ter ao menos 10 caracteres.')
                    user.full_clean(exclude=['password']);user.set_password(password);user.save()
                    target=MembroEmpresa.objects.create(empresa=m.empresa,usuario=user,papel=role)
                    audit(m.empresa,request.user,'MEMBRO_CRIADO',target.pk,papel=role)
            messages.success(request,'Equipe atualizada.')
        except Exception as exc:messages.error(request,str(exc))
        return redirect('gestao-equipe',empresa_id=empresa_id)
    return render(request,'gestao_equipe.html',dict(empresa=m.empresa,membro=m,membros=MembroEmpresa.objects.filter(empresa=m.empresa).select_related('usuario')))
