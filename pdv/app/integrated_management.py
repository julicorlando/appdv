"""Management modules, documents and printing inside the desktop window.

The management renderer uses a temporary, in-memory authenticated profile.
No passwords or API tokens are inserted into a URL or persisted in the renderer.
"""
from pathlib import Path
from urllib.parse import urlencode
from PySide6.QtCore import QUrl, Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QListWidget,QListWidgetItem,QFileDialog,QMessageBox,QSplitter)
from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineHttpRequest,QWebEngineUrlRequestInterceptor,QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtPrintSupport import QPrinter,QPrintDialog
from app.api_client import ArmazemAPI

MODULES = [
    ('Visão geral','painel',''),('Produtos e estoque','produtos','produtos/'),
    ('Clientes','clientes','clientes/'),('Orçamentos','orcamentos','orcamentos/'),
    ('Separação e entregas','entregas','entregas/'),('Depósitos e transferências','depositos','depositos/'),
    ('Trocas e devoluções','devolucoes','devolucoes/'),('Vendas','vendas','vendas/'),
    ('Compras e NF-e de entrada','compras','compras/'),('Reposição e pedidos de compra','reposicao','reposicao/'),
    ('Inventário','inventario','inventario/'),('Conferência de caixas','caixas','caixas/'),
    ('Contas a receber','contas-receber','contas-receber/'),('Contas a pagar','contas-pagar','contas-pagar/'),
    ('Margens e resultados','relatorios','relatorios/'),('Sincronização','sincronizacao','sincronizacao/'),
    ('Autorizações','autorizacoes','autorizacoes/'),('Pedidos confirmados','pedidos-web','pedidos-web/'),
    ('Dados da empresa e logo','configuracao','configuracao/'),('Central fiscal','fiscal','fiscal/'),
    ('Políticas de desconto','descontos','descontos/'),('Equipe e permissões','equipe','equipe/'),
]


def same_origin(value, base):
    url,origin=QUrl(value),QUrl(base)
    return url.scheme()==origin.scheme() and url.host()==origin.host() and url.port(443)==origin.port(443) and not url.userInfo()


def navigation_allowed(value, base, empresa_id=None, local_file=None):
    url=QUrl(value)
    if value=='about:blank':return True
    if local_file is not None:
        return url.isLocalFile() and Path(url.toLocalFile()).resolve()==Path(local_file).resolve()
    if not same_origin(value,base):return False
    path=url.path()
    return (path in ('/','/desktop/session/','/accounts/logout/') or
            path.startswith(f'/empresa/{empresa_id}/') or
            path.startswith('/media/') or path.startswith('/static/') or path.startswith('/pedido/'))


class OriginInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self,base,parent=None,local_file=None):
        super().__init__(parent);self.base=base;self.local_file=local_file
    def interceptRequest(self,info):
        url=info.requestUrl()
        if url.scheme() in ('data','blob','about'):return
        if self.local_file is not None:
            if url.isLocalFile() and Path(url.toLocalFile()).resolve()==Path(self.local_file).resolve():return
            info.block(True);return
        if not same_origin(url.toString(),self.base):info.block(True)


class EmbeddedPage(QWebEnginePage):
    blocked=Signal(str)
    def __init__(self,profile,base,empresa_id,parent=None,local_file=None,popup=None):
        super().__init__(profile,parent);self.base=base;self.empresa_id=empresa_id;self.local_file=local_file;self.popup=popup
    def acceptNavigationRequest(self,url,kind,is_main_frame):
        if navigation_allowed(url.toString(),self.base,self.empresa_id,self.local_file):return True
        self.blocked.emit('Navegação externa bloqueada. Use os módulos do aplicativo ou Reconectar para renovar a sessão.')
        return False
    def createWindow(self,kind):
        return self.popup() if self.popup else None
    def certificateError(self,error):
        self.blocked.emit('A conexão não apresentou certificado válido. Confira o servidor.')
        return False


class PrintTools:
    def _printing_setup(self,view,bar):
        self.view=view;self._printer=None
        printer=QPushButton('Imprimir');printer.clicked.connect(self._print_document);bar.addWidget(printer)
        pdf=QPushButton('Salvar PDF');pdf.clicked.connect(self._save_pdf);bar.addWidget(pdf)
        self.view.printRequested.connect(self._print_document)
        self.view.printFinished.connect(self._printed)
        self.view.page().pdfPrintingFinished.connect(self._pdf_saved)
    def _print_document(self):
        self._printer=QPrinter(QPrinter.PrinterMode.HighResolution)
        if QPrintDialog(self._printer,self).exec()==QDialog.DialogCode.Accepted:self.view.print(self._printer)
        else:self._printer=None
    def _printed(self,ok):
        self._printer=None
        if not ok:QMessageBox.warning(self,'Impressão','Não foi possível enviar o documento à impressora.')
    def _save_pdf(self):
        path,_=QFileDialog.getSaveFileName(self,'Salvar PDF',str(Path.home()/'documento-appdv.pdf'),'PDF (*.pdf)')
        if path:self.view.page().printToPdf(path)
    def _pdf_saved(self,path,ok):
        if not ok:QMessageBox.warning(self,'PDF','Não foi possível salvar o PDF.')


class DocumentPreviewDialog(QDialog,PrintTools):
    def __init__(self,path,parent=None):
        super().__init__(parent);self.setWindowTitle('ApPdv — visualizar documento');self.resize(1050,780)
        self.path=str(Path(path).resolve());self.profile=QWebEngineProfile();self.destroyed.connect(self.profile.deleteLater)
        self.interceptor=OriginInterceptor('',self.profile,local_file=self.path);self.profile.setUrlRequestInterceptor(self.interceptor)
        self.web=QWebEngineView(self)
        page=EmbeddedPage(self.profile,'',None,self.web,local_file=self.path)
        self.web.setPage(page)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,False)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,False)
        layout=QVBoxLayout(self);bar=QHBoxLayout();self._printing_setup(self.web,bar)
        close=QPushButton('Fechar');close.clicked.connect(self.accept);bar.addWidget(close)
        layout.addLayout(bar);layout.addWidget(self.web);self.web.load(QUrl.fromLocalFile(self.path))
    def done(self,result):
        if self._printer:
            QMessageBox.information(self,'Impressão','Aguarde o envio à impressora terminar.');return
        self.web.stop();super().done(result)


class SessionWorker(QThread):
    success=Signal(object);failure=Signal(str)
    def __init__(self,api,area,parent=None):
        super().__init__(parent);self.area=area
        self.client=ArmazemAPI(api.base_url,empresa_id=api.empresa_id)
        self.client.session.headers.update(api.session.headers);self.client.refresh_token=api.refresh_token
    def run(self):
        try:self.success.emit(self.client.iniciar_gestao(self.area))
        except Exception as exc:self.failure.emit(str(exc))


class ManagementDialog(QDialog,PrintTools):
    def __init__(self,api,empresa_id,company_name,role,parent=None):
        super().__init__(parent);self.api=api;self.empresa_id=str(empresa_id);self.role=role
        self.setWindowTitle(f'ApPdv — Gestão · {company_name}');self.resize(1400,900)
        self.setMinimumSize(950,650);self.worker=None;self.authenticated=False;self.children_previews=[]
        layout=QVBoxLayout(self);bar=QHBoxLayout()
        self.status=QLabel('Conectando à gestão…');self.status.setWordWrap(True);bar.addWidget(self.status,1)
        back=QPushButton('Voltar');back.clicked.connect(lambda:self.web.back());bar.addWidget(back)
        reconnect=QPushButton('Reconectar');reconnect.clicked.connect(self._connect);bar.addWidget(reconnect)
        close=QPushButton('Voltar ao caixa');close.clicked.connect(self.accept);bar.addWidget(close)
        layout.addLayout(bar)
        split=QSplitter(Qt.Orientation.Horizontal);self.menu=QListWidget()
        self.modules=[m for m in MODULES if role=='ADMIN' or m[1] not in ('descontos','equipe')]
        for name,area,path in self.modules:self.menu.addItem(name)
        self.menu.setMaximumWidth(280);self.menu.setMinimumWidth(200)
        self.profile=QWebEngineProfile();self.destroyed.connect(self.profile.deleteLater)
        self.interceptor=OriginInterceptor(api.base_url,self.profile);self.profile.setUrlRequestInterceptor(self.interceptor)
        self.profile.downloadRequested.connect(self._download)
        self.web=QWebEngineView(self)
        self.page=EmbeddedPage(self.profile,api.base_url,self.empresa_id,self.web,popup=self._popup)
        self.page.blocked.connect(self.status.setText);self.web.setPage(self.page)
        self._printing_setup(self.web,bar)
        split.addWidget(self.menu);split.addWidget(self.web);split.setStretchFactor(1,1);layout.addWidget(split)
        self.menu.currentRowChanged.connect(self._navigate)
        self.web.loadFinished.connect(self._loaded)
        self.menu.setCurrentRow(0)
    def _current(self):return self.modules[max(0,self.menu.currentRow())]
    def _navigate(self,index):
        if index<0:return
        if not self.authenticated:self._connect();return
        _,area,path=self._current()
        self.status.setText('Carregando '+self._current()[0]+'…')
        self.web.load(QUrl(self.api.base_url+f'/empresa/{self.empresa_id}/'+path))
    def _connect(self):
        if self.worker and self.worker.isRunning():return
        self.status.setText('Autenticando sessão de gestão…');self.menu.setEnabled(False)
        self.worker=SessionWorker(self.api,self._current()[1],self)
        self.worker.success.connect(self._session_ready);self.worker.failure.connect(self._failed);self.worker.start()
    def _session_ready(self,data):
        endpoint=data.get('endpoint','')
        if endpoint!='/desktop/session/':self._failed('Resposta de autenticação inválida.');return
        request=QWebEngineHttpRequest(QUrl(self.api.base_url+endpoint))
        request.setMethod(QWebEngineHttpRequest.Method.Post)
        request.setHeader(b'Content-Type',b'application/x-www-form-urlencoded')
        request.setHeader(b'X-Appdv-Desktop',b'1')
        request.setPostData(urlencode({'ticket':data['ticket']}).encode())
        self.web.load(request);self.authenticated=True
    def _failed(self,message):
        self.authenticated=False;self.menu.setEnabled(True)
        self.status.setText('Gestão indisponível. Verifique a conexão e use Reconectar. '+message)
    def _loaded(self,ok):
        self.menu.setEnabled(True)
        if ok:self.status.setText(self._current()[0]+' · '+self.api.username)
        else:self.status.setText('Não foi possível carregar o módulo. Confira a conexão ou use Reconectar.')
    def _popup(self):
        dialog=ManagementPopup(self.profile,self.api.base_url,self.empresa_id,self)
        dialog.show();self.children_previews.append(dialog)
        return dialog.web.page()
    def _download(self,download):
        filename=Path(download.suggestedFileName()).name
        path,_=QFileDialog.getSaveFileName(self,'Salvar arquivo',str(Path.home()/'Downloads'/filename),'Arquivos (*)')
        if not path:download.cancel();return
        download.setDownloadDirectory(str(Path(path).parent));download.setDownloadFileName(Path(path).name);download.accept()
    def done(self,result):
        if self.worker and self.worker.isRunning():
            self.status.setText('Aguarde a conexão terminar antes de sair.');return
        if self._printer or any(child._printer for child in self.children_previews):
            self.status.setText('Aguarde o envio à impressora terminar.');return
        for child in self.children_previews:child.close()
        self.web.stop();self.profile.cookieStore().deleteAllCookies()
        super().done(result)


class ManagementPopup(QDialog,PrintTools):
    def __init__(self,profile,base,empresa_id,parent):
        super().__init__(parent);self.setWindowTitle('ApPdv — documento');self.resize(1050,780)
        layout=QVBoxLayout(self);bar=QHBoxLayout();self.web=QWebEngineView(self)
        self.web.setPage(EmbeddedPage(profile,base,empresa_id,self.web))
        self._printing_setup(self.web,bar)
        close=QPushButton('Fechar');close.clicked.connect(self.accept);bar.addWidget(close)
        layout.addLayout(bar);layout.addWidget(self.web)
    def done(self,result):
        if self._printer:return
        self.web.stop();super().done(result)
