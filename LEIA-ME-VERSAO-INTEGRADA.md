# Sistema CEN — versão integrada para homologação

Esta cópia consolida as etapas 1–4 e a área de Comunicados com imagens, categorias, anexos e arquivamento. **Não é uma versão homologada para produção**.

## Funcionalidades

- Menu inferior: Início, Boletim, Financeiro, Mensagem, Calendário, Comunicados.
- Início: seleção de aluno vinculado, foto, carteirinha PDF com miniatura e banner gerenciados pelo administrador.
- Boletim: Fundamental I descritivo; Fundamental II e Ensino Médio com conceitos trimestrais, faltas, publicação e disciplinas editáveis.
- Financeiro: boleto PDF individual por aluno, mês e ano, histórico, confirmação manual de pagamento, visualização e download autenticados.
- Mensagens: somente escola para famílias; assunto, data, hora, corpo, leitura e contador. Admin pode enviar para aluno, turma ou todos.
- Calendário: navegação por mês/ano, legendas personalizadas, eventos de um ou mais dias; administração pode cadastrar, editar e excluir.
- Comunicados: mural comum, imagem de destaque, categorias, PDF anexo, rascunho, publicação, fixação e arquivamento.

## Limitações e pendências antes da produção

1. O ambiente de elaboração não possui Flask, Flask-WTF, Flask-Limiter e Werkzeug e não permite instalar essas dependências. Foram feitos testes estáticos de sintaxe Python, análise de templates Jinja2 e testes de migração SQLite, **não** testes completos de execução web.
2. Validar login, CSRF, autorização por aluno, upload e acesso a PDFs com usuários de teste em ambiente isolado.
3. Validar visualmente os boletins com os modelos oficiais da escola e revisar a lista de disciplinas por turma.
4. Criar cópia de segurança verificada do banco e dos arquivos antes de qualquer migração de dados reais.
5. Configurar `SECRET_KEY` fixa e forte, `COOKIE_SECURE=1`, HTTPS, `DATABASE_PATH` e `UPLOAD_ROOT` em disco persistente e backups regulares na Hostinger.
6. O status financeiro é manual, sem integração bancária ou confirmação automática de compensação.
7. A edição de imagens e PDFs substitui o vínculo no banco, mas os arquivos antigos continuam no armazenamento até política de limpeza/retencão ser definida.
8. Verificar aderência à LGPD, permissões, logs, restauração de backups e plano de reversão.

## Inicialização em ambiente de testes

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY='gere-uma-chave-longa-e-aleatoria'
export DATABASE_PATH='/caminho/privado/cen-teste.sqlite3'
export UPLOAD_ROOT='/caminho/privado/uploads'
flask --app app init-db
flask --app app create-admin
flask --app app run
```

Nunca utilizar o banco de dados de produção nos primeiros testes. Os uploads ficam fora de `static/` e são servidos mediante autorização. A execução da migração no banco real deve ser precedida de backup e validação em cópia.
