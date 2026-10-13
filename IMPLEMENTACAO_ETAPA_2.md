# Etapa 2 — Tela Início

- Foto do aluno, PDF da carteirinha e miniatura da primeira página; banner gerenciado pelo administrador.
- Arquivos privados em UPLOAD_ROOT, com verificação de autorização por aluno.
- Configure UPLOAD_ROOT em volume persistente no VPS, com backups. Não use armazenamento efêmero do Render.
- Execute `flask --app app init-db` para migrar a base **após backup**.
- Configure SECRET_KEY fixa no ambiente e HTTPS/COOKIE_SECURE=1 em produção.
- Ainda é uma versão de desenvolvimento; não publicar sem testes e revisão de segurança.
