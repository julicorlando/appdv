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
    class Meta:
        model = Produto
        fields = ("categoria", "codigo", "codigo_barras", "nome", "unidade", "setor", "endereco",
                  "preco_venda", "custo_medio", "estoque_minimo")
        widgets = {
            "preco_venda": forms.NumberInput(attrs={"step": "0.01"}),
            "custo_medio": forms.NumberInput(attrs={"step": "0.01"}),
            "estoque_minimo": forms.NumberInput(attrs={"step": "0.001"}),
        }
    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.fields["categoria"].queryset = Categoria.objects.filter(empresa=empresa, ativa=True)
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
