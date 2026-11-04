# Gestão no aplicativo PC

Administradores e gerentes escolhem **Gestão da empresa** ao entrar ou usam o botão de gestão no caixa. Não é preciso abrir caixa para gerenciar a empresa. Os módulos são apresentados dentro do aplicativo por Qt WebEngine, usando os mesmos cadastros e permissões do servidor. O administrador também pode criar usuários e editar perfis em **Equipe e permissões**; o gerente não pode acessar essa área nem alterar a política de descontos exclusiva do administrador.

Produtos, clientes (incluindo edição), orçamentos, vendas, entregas, depósitos, devoluções, compras, reposição, inventário, caixas, financeiro, relatórios, sincronização, autorizações, pedidos e configurações ficam acessíveis pela navegação integrada. Documentos abrem em visualização interna com impressão e exportação para PDF. O pedido por WhatsApp copia o texto para envio pelo operador.

A gestão exige conexão ao servidor. O funcionamento offline existente continua restrito ao caixa e à fila local de vendas; não há edição administrativa offline. A central fiscal continua dependendo de integração fiscal homologada, certificado e tributação: esta atualização não implementa emissão real de NF-e.

## Atualizar no Windows

Depois de atualizar o repositório, feche o ApPdv, entre na pasta `pdv` e execute `build_windows.bat`. O script instala as dependências de compilação e gera **dist\ApPdv\ApPdv.exe**. Mantenha toda a pasta `ApPdv` junta, inclusive `_internal`: o executável isolado não contém o renderizador. Se o Inno Setup estiver disponível no PATH, também será criado **dist\installer\ApPdv-Setup.exe**.

O pacote passa a usar uma pasta de distribuição para carregar os componentes Qt WebEngine sem a extração temporária do antigo executável único. Isso não garante resolver bloqueios do Windows por antivírus ou outro processo. A compilação e a instalação precisam ser verificadas no Windows; os testes automatizados deste projeto rodam também no Linux.

## Sessão e isolamento

O aplicativo troca seu token da API por um ticket aleatório de uso único com validade de 90 segundos, armazenado no servidor apenas como hash. O ticket é enviado por POST com cabeçalho próprio; nenhum token da API ou senha é colocado na URL. O perfil do renderizador fica em memória. A sessão é vinculada à empresa selecionada e o servidor revalida o papel no resgate. Links externos são bloqueados e downloads exigem escolha do destino. Ao sair, o perfil é descartado.
