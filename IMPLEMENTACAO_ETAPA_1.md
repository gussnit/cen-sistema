# CEN — Etapa 1 (versão de teste)

Implementado: navegação por páginas separadas, mensagens apenas da administração com assunto/data/hora/corpo e marcação de leitura, calendário mensal global, categorias/legendas editáveis pelo administrador, mural de comunicados separado.

**Ainda pendente:** boletim trimestral por conceitos, PDFs de boletos e carteirinhas, banners/fotos, imagens/anexos nos comunicados, edição completa de eventos e fluxo completo do financeiro. Não usar em produção sem testes e backup.

Inicialização: `flask --app app init-db` executa migração aditiva e não apaga registros. Para banco existente, executar este comando **com backup prévio**.

O banco não acompanha este pacote. Em produção configure `SECRET_KEY` fixo e `DATABASE_PATH` persistente.
