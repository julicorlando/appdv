"""Single-use authentication handoff to an in-app management profile."""
import hashlib
import secrets
from datetime import timedelta
from django.contrib.auth import login
from django.db import transaction
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from .api import TenantAPIView
from .models import SessaoDesktopTicket, MembroEmpresa

AREAS = {'painel':'','produtos':'produtos/','compras':'compras/','inventario':'inventario/',
         'clientes':'clientes/','orcamentos':'orcamentos/','entregas':'entregas/',
         'depositos':'depositos/','devolucoes':'devolucoes/','caixas':'caixas/',
         'sincronizacao':'sincronizacao/','relatorios':'relatorios/','reposicao':'reposicao/',
         'descontos':'descontos/','configuracao':'configuracao/','fiscal':'fiscal/',
         'contas-receber':'contas-receber/','contas-pagar':'contas-pagar/',
         'autorizacoes':'autorizacoes/','vendas':'vendas/','pedidos-web':'pedidos-web/', 'equipe':'equipe/',
         'fornecedores':'fornecedores/','etiquetas':'etiquetas/','relatorios-gerenciais':'relatorios/gerenciais/'}


class DesktopSessionView(TenantAPIView):
    def post(self,request):
        empresa,membro=self.get_empresa(request)
        if membro.papel not in ['ADMIN','GERENTE']:
            raise PermissionDenied('Gestão disponível apenas para gerente ou administrador.')
        area=request.data.get('area','painel')
        if area not in AREAS:
            raise ValidationError('Módulo administrativo inválido.')
        if area in ['descontos','equipe'] and membro.papel!='ADMIN':
            raise PermissionDenied('Módulo exclusivo do administrador.')
        token=secrets.token_urlsafe(48)
        SessaoDesktopTicket.objects.filter(expira_em__lt=timezone.now()-timedelta(days=1)).delete()
        SessaoDesktopTicket.objects.create(resumo=hashlib.sha256(token.encode()).hexdigest(),usuario=request.user,
            empresa=empresa,area=area,expira_em=timezone.now()+timedelta(seconds=90))
        response=Response({'ticket':token,'endpoint':'/desktop/session/'})
        response['Cache-Control']='no-store'
        return response


@csrf_exempt
@transaction.atomic
def iniciar_sessao_desktop(request):
    if request.method!='POST':return HttpResponseNotAllowed(['POST'])
    # A cross-site HTML form cannot supply this header. CORS is not enabled here.
    if request.headers.get('X-Appdv-Desktop')!='1':return HttpResponseForbidden('Origem inválida.')
    token=request.POST.get('ticket','')
    if not 32<=len(token)<=128:return HttpResponseForbidden('Sessão inválida.')
    obj=SessaoDesktopTicket.objects.select_for_update().filter(resumo=hashlib.sha256(token.encode()).hexdigest(),
        usada_em__isnull=True,expira_em__gt=timezone.now()).select_related('usuario','empresa').first()
    if not obj or not obj.usuario.is_active or not obj.empresa.ativa:return HttpResponseForbidden('Sessão inválida ou expirada.')
    profile=getattr(obj.usuario,'perfil_armazem',None)
    if profile and not profile.ativo:return HttpResponseForbidden('Conta desativada.')
    member=MembroEmpresa.objects.filter(empresa=obj.empresa,usuario=obj.usuario,ativo=True,papel__in=['ADMIN','GERENTE']).first()
    if not member or (obj.area in ['descontos','equipe'] and member.papel!='ADMIN'):return HttpResponseForbidden('Permissão revogada.')
    obj.usada_em=timezone.now();obj.save(update_fields=['usada_em'])
    login(request,obj.usuario,backend='django.contrib.auth.backends.ModelBackend')
    request.session['desktop_empresa']=obj.empresa_id
    response=redirect(f'/empresa/{obj.empresa_id}/'+AREAS[obj.area])
    response['Cache-Control']='no-store'
    return response


class DesktopTenantMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        empresa=request.session.get('desktop_empresa')
        if empresa:
            path=request.path
            if path=='/':return redirect(f'/empresa/{empresa}/')
            if path.startswith(('/master/','/admin/')):return HttpResponseForbidden('Use a gestão da empresa selecionada.')
            if path.startswith('/api/') and request.headers.get('X-Empresa-ID') and str(request.headers['X-Empresa-ID'])!=str(empresa):
                return HttpResponseForbidden('Empresa diferente da sessão administrativa.')
            if path.startswith('/empresa/') and not path.startswith(f'/empresa/{empresa}/'):
                return HttpResponseForbidden('A sessão está vinculada à empresa selecionada no aplicativo.')
        return self.get_response(request)
