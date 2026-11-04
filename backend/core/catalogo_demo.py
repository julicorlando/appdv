from decimal import Decimal

from django.db import transaction

from .models import Categoria, Produto


LINHAS = {
    "Hidráulica": ("Joelho PVC", "Tê PVC", "Luva PVC", "Registro", "Adaptador", "União", "Cap PVC", "Niple", "Redução", "Flange"),
    "Elétrica": ("Disjuntor", "Tomada", "Interruptor", "Conduíte", "Conector", "Caixa elétrica", "Terminal", "Canaleta", "Abraçadeira", "Soquete"),
    "Ferragens": ("Parafuso", "Porca", "Arruela", "Bucha", "Prego", "Dobradiça", "Fecho", "Cantoneira", "Chumbador", "Gancho"),
    "Pintura": ("Tinta acrílica", "Esmalte", "Selador", "Verniz", "Rolo", "Pincel", "Bandeja", "Lixa", "Espátula", "Fita de pintura"),
    "Construção": ("Cimento", "Argamassa", "Rejunte", "Areia", "Brita", "Bloco", "Tijolo", "Telha", "Vergalhão", "Piso"),
}


@transaction.atomic
def carregar_catalogo_demo(empresa):
    criados = 0
    for setor, (categoria_nome, linhas) in enumerate(LINHAS.items(), 1):
        categoria, _ = Categoria.objects.get_or_create(
            empresa=empresa, nome=f"Teste · {categoria_nome}",
        )
        for indice, linha in enumerate(linhas, 1):
            for variante in range(1, 5):
                numero = (setor - 1) * 40 + (indice - 1) * 4 + variante
                base = f"200987{numero:06d}"
                digito = -sum(int(valor) * (1 if i % 2 == 0 else 3) for i, valor in enumerate(base)) % 10
                preco = Decimal(350 + numero * 137) / 100
                _, criado = Produto.objects.get_or_create(
                    empresa=empresa, codigo=f"DEMO-{numero:04d}",
                    defaults={
                        "categoria": categoria,
                        "nome": f"[TESTE] {linha} · modelo {variante}",
                        "codigo_barras": f"{base}{digito}",
                        "setor": f"Teste {categoria_nome}",
                        "endereco": f"T{setor:02d}-P{indice:02d}-N{variante}",
                        "unidade": "SC" if linha in {"Cimento", "Argamassa", "Rejunte", "Areia", "Brita"} else "UN",
                        "preco_venda": preco,
                        "custo_medio": (preco * Decimal("0.65")).quantize(Decimal("0.01")),
                        "estoque_atual": 2 + numero % 120,
                        "estoque_minimo": 10,
                    },
                )
                criados += criado
    return criados
