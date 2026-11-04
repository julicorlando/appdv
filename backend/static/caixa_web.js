"use strict";
const pos = document.querySelector(".web-pos");
const field = (id) => document.getElementById("pos-" + id);
const money = (value) => Number(value).toLocaleString("pt-BR", {style: "currency", currency: "BRL"});
const cart = new Map();
let cash = pos.dataset.caixa;
let saleKey = crypto.randomUUID();
let receipt = "";
let locked = false;
let discountCents = 0;
const discountPolicy = JSON.parse(document.getElementById("pos-discount-policy").textContent);

async function api(path, body) {
    const response = await fetch("/api/v1/" + path, {
        method: body === undefined ? "GET" : "POST",
        headers: {
            "X-Empresa-ID": pos.dataset.empresa,
            "X-CSRFToken": pos.querySelector("[name=csrfmiddlewaretoken]").value,
            "Content-Type": "application/json",
        },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(Object.values(data).flat().join(" "));
    return data;
}

function run(action) {
    return async () => {
        if (locked) return;
        locked = true;
        field("message").textContent = "";
        try { await action(); }
        catch (error) { field("message").textContent = error.message; }
        finally { locked = false; }
    };
}

function cents(item) {
    return Math.round(Number(item.product.preco_venda) * item.quantity * 100);
}

function total() {
    return ([...cart.values()].reduce((sum, item) => sum + cents(item), 0) - discountCents) / 100;
}

function render() {
    field("status").textContent = cash ? "Caixa aberto" : "Caixa fechado";
    field("open").disabled = Boolean(cash);
    for (const id of ["summary", "movement", "close"]) field(id).disabled = !cash;
    field("pay").disabled = !cash || !cart.size;
    field("print").disabled = !cart.size;
    const lastItem = [...cart.values()].at(-1);
    field("current").textContent = lastItem?.product.nome || "Aguardando produto";
    field("total").textContent = money(total());
    field("cart").replaceChildren();
    for (const [id, item] of cart) {
        const row = field("cart").insertRow();
        row.insertCell().textContent = item.product.nome;
        const quantity = document.createElement("input");
        quantity.type = "number";
        quantity.min = "0.001";
        quantity.step = "0.001";
        quantity.value = item.quantity;
        quantity.setAttribute("aria-label", "Quantidade de " + item.product.nome);
        quantity.onchange = () => {
            if (locked || !quantity.checkValidity() || !Number(quantity.value)) {
                quantity.value = item.quantity;
                return;
            }
            item.quantity = Number(quantity.value);
            render();
        };
        row.insertCell().append(quantity);
        row.insertCell().textContent = money(cents(item) / 100);
        const remove = document.createElement("button");
        remove.textContent = "Remover";
        remove.onclick = () => { if (!locked) { cart.delete(id); render(); } };
        row.insertCell().append(remove);
    }
}

let searchVersion = 0;
field("search").oninput = async () => {
    const version = ++searchVersion;
    const query = field("search").value.trim();
    field("products").replaceChildren();
    if (!query) return;
    try {
        const data = await api("produtos/?search=" + encodeURIComponent(query));
        if (version !== searchVersion) return;
        for (const product of (data.results || data).slice(0, 20)) {
            const button = document.createElement("button");
            button.className = "secondary";
            button.textContent = product.codigo + " · " + product.nome + " · " +
                money(product.preco_venda) + " · Saldo " + product.estoque_atual;
            button.onclick = () => {
                if (locked) return;
                const current = cart.get(product.id);
                cart.set(product.id, {product, quantity: (current?.quantity || 0) + 1});
                field("search").value = "";
                field("products").replaceChildren();
                field("search").focus();
                render();
            };
            field("products").append(button);
        }
    } catch (error) {
        if (version === searchVersion) field("message").textContent = error.message;
    }
};
field("search").onkeydown = (event) => {
    if (event.key === "Enter" && field("products").children.length === 1) {
        field("products").firstElementChild.click();
    }
};
field("open").onclick = run(async () => {
    const amount = prompt("Saldo inicial em dinheiro", "0");
    if (amount === null) return;
    const data = await api("caixas/abrir/", {dispositivo_id: "web", saldo_abertura: amount.replace(",", ".")});
    cash = data.id;
    render();
});
field("summary").onclick = run(async () => {
    const data = await api("caixas/" + cash + "/resumo/");
    const summary = data.resumo || data;
    field("summary-text").textContent = "Dinheiro esperado: " + money(summary.esperado_dinheiro);
    field("summary-text").hidden = false;
});
field("movement").onclick = run(async () => {
    const type = prompt("Digite SUPRIMENTO ou SANGRIA");
    if (!type) return;
    const amount = prompt("Valor");
    if (amount === null) return;
    const reason = prompt("Motivo");
    if (reason === null) return;
    await api("caixas/movimentacoes/", {caixa: cash, tipo: type.trim().toUpperCase(), valor: amount.replace(",", "."), motivo: reason});
    field("message").textContent = "Movimentação registrada.";
});
field("close").onclick = run(async () => {
    const amount = prompt("Dinheiro contado no caixa");
    if (amount === null) return;
    const data = await api("caixas/" + cash + "/fechar/", {saldo_fechamento: amount.replace(",", ".")});
    cash = "";
    field("summary-text").hidden = true;
    field("message").textContent = "Caixa fechado. Dinheiro esperado: " + money(data.resumo.esperado_dinheiro);
    render();
});
field("pay").onclick = () => {
    updatePaymentDiscount();
    field("payment").showModal();
};
field("cancel-pay").onclick = () => { discountCents = 0; field("payment").close(); render(); };
function printable(title) {
    return [title, new Date().toLocaleString("pt-BR"), ...[...cart.values()].map(item =>
        item.quantity + " × " + item.product.nome + " — " + money(cents(item) / 100)
    ), "Total: " + money(total())].join("\n");
}
function printText(text) {
    const popup = window.open("", "_blank", "popup");
    if (!popup) { field("message").textContent = "Permita janelas pop-up para imprimir."; return; }
    popup.document.title = "ApPdv — impressão";
    const style = popup.document.createElement("style");
    style.textContent = "body{font:13px Arial;color:#000;background:#fff;max-width:76mm;margin:8mm auto}header{text-align:center;border-bottom:1px dashed #000;padding-bottom:10px}img{display:block;max-width:60mm;max-height:20mm;margin:8px auto;object-fit:contain}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.6}@media print{body{margin:0;max-width:100%}button{display:none}}";
    popup.document.head.append(style);
    const header = popup.document.createElement("header");
    const images = [];
    for (const [src, label] of [[pos.dataset.systemLogo, "ApPdv"], [pos.dataset.companyLogo, pos.dataset.company]]) {
        if (!src) continue;
        const image = popup.document.createElement("img");
        image.alt = label;
        image.src = new URL(src, window.location.href).href;
        header.append(image);
        images.push(image);
    }
    const name = popup.document.createElement("strong");
    name.textContent = pos.dataset.company;
    header.append(name);
    const details = JSON.parse(document.getElementById("pos-company-details").textContent);
    const lines = [details.razao_social, details.cnpj && "CNPJ: " + details.cnpj,
        details.inscricao_estadual && "IE: " + details.inscricao_estadual,
        [details.logradouro, details.numero, details.complemento, details.bairro, details.municipio, details.uf, details.cep].filter(Boolean).join(", "),
        [details.telefone, details.email].filter(Boolean).join(" · ")];
    for (const line of lines.filter(Boolean)) {
        const paragraph = popup.document.createElement("div");
        paragraph.textContent = line;
        header.append(paragraph);
    }
    const page = popup.document.createElement("pre");
    page.textContent = text;
    const button = popup.document.createElement("button");
    button.textContent = "Imprimir";
    button.onclick = () => popup.print();
    popup.document.body.append(header, page, button);
    // Aguarda as logos antes de abrir o diálogo de impressão.
    Promise.all(images.map(image => image.decode().catch(() => {}))).then(() => {
        if (!popup.closed) { popup.focus(); popup.print(); }
    });
}
field("print").onclick = () => printText(printable("ORÇAMENTO"));
field("receipt").onclick = () => printText(receipt);
function updatePaymentDiscount() {
    const gross = [...cart.values()].reduce((sum, item) => sum + cents(item), 0);
    const methods = [...new FormData(field("payment-form"))].filter(([key,value]) => ["DINHEIRO","PIX","CARTAO","OUTRO"].includes(key) && Number(value)>0).map(([key])=>key);
    let auto = 0;
    if (discountPolicy.modo !== "MANUAL" && gross >= Math.round(Number(discountPolicy.minimo)*100) &&
        (!discountPolicy.meio || (methods.length===1 && methods[0]===discountPolicy.meio))) auto = Math.round(gross*Number(discountPolicy.percentual)/100);
    const raw = Math.max(0,Number(field("discount").value)||0);
    const manual = field("discount-mode").value === "%" ? Math.round(gross*raw/100) : Math.round(raw*100);
    discountCents = Math.min(gross,Math.max(auto,manual));
    field("discount-summary").textContent = "Desconto aplicado: " + money(discountCents/100) + " · Total: " + money(total());
    field("due").textContent = "Subtotal: " + money(gross/100) + " · Total a pagar: " + money(total());
}
field("payment-form").oninput = updatePaymentDiscount;
field("payment-form").onsubmit = (event) => {
    event.preventDefault();
    return run(async () => {
        updatePaymentDiscount();
        const payments = [...new FormData(event.target)].filter(([key,value]) => ["DINHEIRO","PIX","CARTAO","OUTRO"].includes(key) && Number(value)>0).map(([meio,valor])=>({meio,valor}));
        const gross = [...cart.values()].reduce((sum,item)=>sum+cents(item),0);
        const entries = [...cart];
        const allocations = entries.map(([,item])=>gross ? Math.floor(discountCents*cents(item)/gross) : 0);
        let remaining = discountCents-allocations.reduce((sum,value)=>sum+value,0);
        for(let index=0;index<entries.length && remaining>0;index++) {
            const extra=Math.min(remaining,cents(entries[index][1])-allocations[index]);
            allocations[index]+=extra;
            remaining-=extra;
        }
        const items = entries.map(([produto,item],index)=>({produto,quantidade:String(item.quantity),desconto:(allocations[index]/100).toFixed(2),preco_esperado:String(item.product.preco_venda)}));
        const cashPayment=payments.find(p=>p.meio==='DINHEIRO');
        const otherCents=payments.filter(p=>p.meio!=='DINHEIRO').reduce((sum,p)=>sum+Math.round(Number(p.valor)*100),0);
        const dueCents=Math.round(total()*100);
        if(otherCents>dueCents) throw new Error('Pagamentos sem dinheiro ultrapassam o total.');
        if(cashPayment) {
            const received=Math.round(Number(cashPayment.valor)*100);
            const applied=dueCents-otherCents;
            if(applied<=0 || received<applied) throw new Error('Confira o valor em dinheiro e as demais formas de pagamento.');
            cashPayment.valor=(applied/100).toFixed(2);
            cashPayment.valor_recebido=(received/100).toFixed(2);
            cashPayment.troco=((received-applied)/100).toFixed(2);
        }
        let authorization;
        if (field("supervisor").value || field("supervisor-password").value) {
            try { authorization = (await api("descontos/autorizar/",{chave:saleKey,itens:items,usuario:field("supervisor").value,senha:field("supervisor-password").value})).codigo; }
            finally { field("supervisor-password").value = ""; }
        }
        const data = await api("vendas/", {caixa:cash,idempotency_key:saleKey,pagamentos:payments,itens:items,autorizacao_desconto:authorization});
        receipt = printable("COMPROVANTE NÃO FISCAL · VENDA #" + data.id) + "\nDesconto: " + money(discountCents/100) + "\n" + payments.map(p=>p.meio+": "+money(p.valor)+(Number(p.troco)>0 ? " · Troco: "+money(p.troco) : "")).join("\n");
        field("receipt").hidden = false;
        field("payment").close();
        event.target.reset();
        discountCents = 0;
        cart.clear();
        saleKey = crypto.randomUUID();
        field("message").textContent = "Venda registrada.";
        render();
    })();
};
render();
