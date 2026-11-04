import base64
import html
from pathlib import Path


def quote_html(quote, company, system_logo):
    esc = lambda value: html.escape(str(value or ''), quote=True)
    logo_path = Path(system_logo)
    system_image = 'data:image/png;base64,' + base64.b64encode(logo_path.read_bytes()).decode('ascii') if logo_path.exists() else ''
    images = ''.join(f"<img src='{esc(src)}' alt='{esc(label)}'>" for src, label in [(system_image, 'ApPdv'), (company.get('logo_data_url'), company.get('nome'))] if src)
    identity = [company.get('nome'), company.get('razao_social'), 'CNPJ: ' + company['cnpj'] if company.get('cnpj') else '', company.get('inscricao_estadual'), company.get('endereco_documento'), company.get('telefone'), company.get('email')]
    rows = ''.join(f"<tr><td>{esc(i['codigo'])} · {esc(i['nome'])}</td><td>{esc(i['quantidade'])} {esc(i['unidade'])}</td><td>{esc(i['preco_venda'])}</td><td>{esc(i['desconto'])}</td><td>{esc(i['total'])}</td></tr>" for i in quote['itens'])
    return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Orçamento {esc(quote['id'])}</title>
<style>body{{font:14px Arial;color:#111;margin:24px}}header img{{max-width:200px;max-height:90px;object-fit:contain;margin-right:24px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #ccc;text-align:left}}@media print{{button{{display:none}}}}</style></head><body><header>{images}{''.join('<div>'+esc(line)+'</div>' for line in identity if line)}</header>
<h1>Orçamento nº {esc(quote['id'])}</h1><p>Cliente: {esc(quote['cliente']['nome'])} · CPF: {esc(quote['cliente']['documento'])}</p>
<p>Criado: {esc(quote['criado_em'])} · Validade: {esc(quote['validade'])} · Estado: {esc(quote['estado'])}</p>
<table><thead><tr><th>Produto</th><th>Quantidade</th><th>Preço</th><th>Desconto</th><th>Total</th></tr></thead><tbody>{rows}</tbody></table><h2>Total: R$ {esc(quote['total'])}</h2>
<p>Orçamento não fiscal. Não representa reserva ou baixa de estoque. Preços e disponibilidade serão conferidos na conversão em venda. Retirada no balcão; entrega e frete devem ser combinados no pedido de entrega.</p><button onclick="window.print()">Imprimir orçamento</button></body></html>'''
