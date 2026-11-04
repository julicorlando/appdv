from decimal import Decimal
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils.text import slugify
from .models import Categoria, Empresa, MembroEmpresa, Produto

User = get_user_model()


class EmpresaForm(forms.Form):
    nome = forms.CharField(max_length=180)
    cnpj = forms.CharField(max_length=14, required=False)
    slug = forms.SlugField(max_length=180)
    username_admin = forms.CharField(max_length=150, label="Usuário do administrador")
    email_admin = forms.EmailField(label="E-mail do administrador")
    senha_admin = forms.CharField(min_length=10, widget=forms.PasswordInput, label="Senha inicial")

    def clean_slug(self):
        slug = slugify(self.cleaned_data["slug"])
        if Empresa.objects.filter(slug=slug).exists():
            raise ValidationError("Este identificador já está em uso.")
        return slug

    def clean_username_admin(self):
        username = self.cleaned_data["username_admin"]
        if User.objects.filter(username=username).exists():
            raise ValidationError("Este usuário já existe. Adicione-o como membro de uma empresa.")
        return username


class MembroForm(forms.Form):
    usuario = forms.CharField(max_length=150, help_text="Nome de usuário existente")
    papel = forms.ChoiceField(choices=MembroEmpresa.Papel.choices)

    def clean_usuario(self):
        username = self.cleaned_data["usuario"]
        try:
            return User.objects.get(username=username, is_active=True)
        except User.DoesNotExist:
            raise ValidationError("Não encontrei usuário ativo com esse nome.")


class CategoriaForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ("nome",)


class ProdutoForm(forms.ModelForm):
    fator_conversao = forms.DecimalField(
        min_value=Decimal("0.000001"), max_digits=12, decimal_places=6,
        help_text="Quantas unidades de estoque são consumidas por unidade vendida.",
    )
    class Meta:
        model = Produto
        fields = ("categoria", "codigo", "codigo_barras", "nome", "unidade",
                  "unidade_estoque", "fator_conversao", "setor", "endereco",
                  "preco_venda", "custo_medio", "estoque_minimo")
        widgets = {
            "preco_venda": forms.NumberInput(attrs={"step": "0.01"}),
            "custo_medio": forms.NumberInput(attrs={"step": "0.01"}),
            "estoque_minimo": forms.NumberInput(attrs={"step": "0.001"}),
            "fator_conversao": forms.NumberInput(attrs={"step": "0.000001", "min": "0.000001"}),
        }
    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.fields["categoria"].queryset = Categoria.objects.filter(empresa=empresa, ativa=True)
        self.fields["unidade_estoque"].label = "Unidade de estoque (base)"
        self.fields["unidade_estoque"].help_text = "Deixe em branco se for igual à unidade de venda."
        self.fields["setor"].required = True
        self.fields["endereco"].required = True

    def clean_codigo(self):
        codigo = self.cleaned_data["codigo"]
        qs = Produto.objects.filter(empresa=self.empresa, codigo=codigo)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("Já existe produto com este código nesta empresa.")
        return codigo

class CategoriaSimpleForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ("nome",)


class EmpresaLogoForm(forms.ModelForm):
    class Meta:
        model = Empresa
        fields = ("nome", "razao_social", "cnpj", "inscricao_estadual", "regime_tributario", "logradouro", "numero", "complemento", "bairro", "municipio", "codigo_municipio", "uf", "cep", "telefone", "email", "ambiente_fiscal", "provedor_fiscal", "logo", "frete_padrao")
        labels = {"frete_padrao": "Frete padrão para entrega (R$)"}
        widgets = {"logo": forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"})}

    def clean_cnpj(self):
        valor = self.cleaned_data["cnpj"]
        if valor and (not valor.isascii() or not valor.isdigit() or len(valor) != 14):
            raise ValidationError("Informe os 14 dígitos do CNPJ, sem pontuação.")
        return valor

    def clean_cep(self):
        valor = self.cleaned_data["cep"]
        if valor and (not valor.isascii() or not valor.isdigit() or len(valor) != 8):
            raise ValidationError("Informe os 8 dígitos do CEP, sem pontuação.")
        return valor

    def clean_codigo_municipio(self):
        valor = self.cleaned_data["codigo_municipio"]
        if valor and (not valor.isascii() or not valor.isdigit() or len(valor) != 7):
            raise ValidationError("Informe os 7 dígitos do código IBGE.")
        return valor

    def clean_uf(self):
        valor = self.cleaned_data["uf"].upper()
        if valor and valor not in "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split():
            raise ValidationError("Selecione uma UF válida.")
        return valor

    def clean_logo(self):
        imagem = self.cleaned_data.get("logo")
        if imagem and hasattr(imagem, "size") and imagem.size > 2 * 1024 * 1024:
            raise ValidationError("A logo deve ter no máximo 2 MB.")
        return imagem
