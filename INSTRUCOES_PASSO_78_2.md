# PASSO 78.2 — Cadastro de notificações Push (Android e iPhone)

## O que está incluído
- Botão de ativação/desativação na área do responsável.
- Permissão de notificação solicitada somente após o toque no botão.
- Registro da inscrição Push no PostgreSQL associado ao usuário autenticado.
- Service worker preparado para receber avisos genéricos, sem informações pessoais.
- **Ainda não há envio de notificações**. O teste de envio é a próxima etapa.

## Antes de ativar
1. Na branch `teste-cen`, substitua `app.py`, `templates/base.html`, `static/sw.js` e adicione `static/push-settings.js`.
2. Execute a inicialização de banco da aplicação: `flask --app app init-db` no ambiente de teste, com as variáveis atuais do PostgreSQL. Ela cria `push_subscriptions` sem apagar dados existentes.
3. Gere um par de chaves VAPID **fora do repositório**, por exemplo usando `npx web-push generate-vapid-keys` no seu computador (Node.js + pacote web-push). Guarde a chave privada em local seguro.
4. Configure no Render **apenas do ambiente de teste** `VAPID_PUBLIC_KEY` com a chave pública. Guarde `VAPID_PRIVATE_KEY` como variável secreta para o próximo passo (envio). **Nunca publique as chaves privadas no GitHub nem envie em prints.**
5. Aguarde o deploy, abra o CEN instalado no iPhone ou Android, faça login como responsável e toque em **Ativar notificações**. Aceite a permissão.
6. Confirme a mensagem “Notificações ativadas neste aparelho!”. Isso confirma a inscrição, não o envio.

### Observações
- No iPhone, use o aplicativo adicionado à Tela de Início (iOS 16.4+), não uma aba do Safari.
- Cada responsável precisa autorizar em cada aparelho.
- O envio futuro deve conferir a vinculação atual de aluno/responsável antes de disparar, e não incluir notas, dados financeiros ou nomes de alunos no aviso da tela bloqueada.
- Se não configurar a chave pública, o portal continua funcionando e mostra “Aguardando configuração do serviço de notificações”.
- O script de configuração usa CSRF, HTTPS e sessão autenticada.
- Se houver falha, não mexa na branch `main`.
