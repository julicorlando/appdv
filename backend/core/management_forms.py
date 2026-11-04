from decimal import Decimal
from django import forms
from django.forms import formset_factory
from .models import *


class ClienteEditForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ['nome','documento','telefone','email','endereco','limite_credito','ativo']
        labels = {'documento':'CPF/CNPJ','endereco':'Endereço','limite_credito':'Limite de crédito (R$)'}
    def __init__(self,*args,empresa,**kwargs):
        super().__init__(*args,**kwargs)
        self.empresa = empresa
        self.instance.empresa = empresa
        self.fields['limite_credito'].min_value = 0
    def clean_documento(self):
        doc = ''.join(c for c in self.cleaned_data['documento'] if c.isascii() and c.isdigit())
        if doc and len(doc) not in (11,14):
            raise forms.ValidationError('Informe CPF com 11 ou CNPJ com 14 dígitos.')
        if doc and Cliente.objects.filter(empresa=self.empresa,documento=doc).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Documento já cadastrado nesta empresa.')
        return doc
    def clean_limite_credito(self):
        value = self.cleaned_data['limite_credito']
        if value < 0:
            raise forms.ValidationError('Limite não pode ser negativo.')
        return value


class QuoteForm(forms.Form):
    cliente = forms.ModelChoiceField(queryset=Cliente.objects.none())
    validade = forms.DateField(widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    supervisor = forms.CharField(required=False,label='Usuário gerente/admin (desconto acima do limite)')
    senha = forms.CharField(required=False,widget=forms.PasswordInput,label='Senha de autorização')
    chave = forms.UUIDField(widget=forms.HiddenInput)
    def __init__(self,*args,empresa,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['cliente'].queryset = Cliente.objects.filter(empresa=empresa,ativo=True)


class LineForm(forms.Form):
    produto = forms.ModelChoiceField(queryset=Produto.objects.none())
    quantidade = forms.DecimalField(min_value=Decimal('.001'),max_digits=14,decimal_places=3)
    desconto = forms.DecimalField(min_value=0,max_digits=12,decimal_places=2,initial=0)
    def __init__(self,*args,empresa,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['produto'].queryset = Produto.objects.filter(empresa=empresa,ativo=True)


QuoteLines = formset_factory(LineForm,extra=1,can_delete=True,max_num=100,validate_max=True)


class DepositoForm(forms.ModelForm):
    class Meta:
        model = Deposito
        fields = ['nome']


class PoliticaForm(forms.ModelForm):
    class Meta:
        model = PoliticaDesconto
        fields = ['modo','limite_caixa','percentual','meio','minimo']
        labels = {'limite_caixa':'Limite manual do caixa (%)','percentual':'Desconto automático (%)','meio':'Forma de pagamento','minimo':'Subtotal mínimo (R$)'}
    def clean(self):
        data = super().clean()
        for field in ['limite_caixa','percentual']:
            if data.get(field,0) > 100:
                self.add_error(field,'Máximo de 100%.')
        return data
