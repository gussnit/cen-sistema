# CEN — Sistema escolar web

Aplicação web responsiva com Flask, SQLite, login por e-mail e senha, contas de administração e responsáveis, matrículas, notas, financeiro informativo, mural, eventos e mensagens. A escola altera os dados e os responsáveis vinculados consultam as informações persistidas no servidor.

## Rodar localmente (Python 3.11+)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m flask --app app init-db
python -m flask --app app create-admin
python -m flask --app app run
```

Abra http://127.0.0.1:5000 no navegador. Entre com o e-mail e a senha cadastrados. O painel Admin permite cadastrar alunos, responsáveis, associar responsáveis a alunos e publicar informações. A página de cada aluno permite atualizar notas, cobranças e mensagens.

## Publicação online

Use hospedagem Python com HTTPS, armazenamento persistente e backups. Configure `SECRET_KEY` com uma chave longa aleatória, `COOKIE_SECURE=1` e `DATABASE_PATH` apontando para um volume persistente. Com SQLite, use somente **uma instância** do servidor e um volume confiável. Exemplo de comando de inicialização da aplicação:

```bash
gunicorn --workers 1 --bind 0.0.0.0:${PORT:-8000} app:app
```

Execute `flask --app app init-db` e `flask --app app create-admin` no servidor antes de abrir o acesso. **Não publique o servidor de desenvolvimento Flask.**

## Antes de usar dados reais de menores de idade

Este é um **MVP funcional**, não uma implantação de produção certificada. Providencie análise LGPD, contrato com hospedagem, políticas de retenção, gestão de consentimentos/base legal, procedimento de resposta a incidentes, backups testados, recuperação de conta, autenticação multifator para administradores, trilha de auditoria acessível, monitoramento, política de senhas, verificação de permissões em todas as rotas, proteção antiabuso, testes de segurança e revisão de infraestrutura. Não inclua informações pessoais reais até concluir essa etapa.

Limitações atuais: não há geração de boleto/PDF, gateway bancário, importação em lote, redefinição de senha por e-mail, exclusão/edição de comunicados e eventos, nem notificações push. Os pagamentos são **registros informativos**. O acesso pelo celular funciona como site responsivo; instalação PWA/offline não está incluída.

## Identidade visual
O cabeçalho usa a logomarca oficial fornecida pela escola, armazenada em `static/logo-cen.png`. As cores principais foram alinhadas ao azul da marca.
