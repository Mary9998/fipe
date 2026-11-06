# Dashboard de Preços FIPE — Pipeline DevOps
 
Dashboard Flask que acompanha a evolução do preço de veículos na **Tabela FIPE**,
com dados guardados em um **PostgreSQL da Aiven.io**. Um pipeline no **GitHub Actions**
testa o código, constrói a imagem Docker, publica no GHCR e faz o deploy no **Render**.
 
Adaptação do laboratório original (dados do Banco Central) para outra fonte de dados abertos.
 
## Tecnologias
 
Python/Flask · PostgreSQL (Aiven) · Docker · GitHub Actions · GHCR · Render
 
## Estrutura
 
```
.
├── Dockerfile               # na raiz; copia a pasta app/
├── app/
│   ├── app.py               # rotas Flask, cache e dashboard
│   ├── fipe_client.py       # APIs de FIPE + acesso ao PostgreSQL
│   ├── requirements.txt
│   ├── templates/           # dashboard.html
│   └── static/              # style.css
└── .github/workflows/
    ├── ci-cd.yml            # testes, build, push e deploy
    └── refresh-data.yml     # atualização periódica dos preços
```
 
## Fonte de dados
 
Preços da Tabela FIPE, consultados na **API v2 da FIPE** (`fipe.parallelum.com.br`),
com a **BrasilAPI** como reserva se a primeira falhar. Nenhuma exige chave.
 
Os veículos acompanhados ficam no dicionário `SERIES` em `app/fipe_client.py`
(chave = código FIPE do modelo):
 
```python
SERIES = {
    "001494-0": {"name": "ARGO DRIVE 1.0 6V Flex", "ano_modelo": None, "unit": "R$", "casas": 2},
}
```
 
Os preços ficam na tabela `fipe_precos`. A cada visita, se o cache tiver mais de
`CACHE_HOURS` (padrão 24h), o app busca preços novos; se as APIs caírem, continua
mostrando o que já está no banco.
 
## Como rodar
 
1. **Aiven:** crie um serviço PostgreSQL (de preferência no plano gratuito) e copie o **Service URI**.
2. **Render:** crie um Web Service com Docker apontando para este repositório
   (Root Directory vazio, Dockerfile Path `./Dockerfile`, Build Context `.`).
3. **Variáveis no Render:** `AIVEN_DB_URL` (o Service URI) e `PORT=8080`.
4. **Secrets no GitHub:**
   | Secret | Valor |
   |---|---|
   | `RENDER_DEPLOY_HOOK_URL` | Deploy Hook do serviço no Render |
   | `APP_HEALTHCHECK_URL` | URL pública do app, sem barra no final |
5. **Deploy:** um `push` na `main` (em `app/` ou no `Dockerfile`) dispara o pipeline:
   testes e lint → build e push da imagem → deploy no Render → verificação de `/healthz`.
## Endpoints
 
| Rota | Descrição |
|---|---|
| `GET /` | Dashboard |
| `GET /api/indicators` | Dados em JSON |
| `POST /api/refresh` | Força a atualização dos preços |
| `GET /healthz` | Health check (testa o banco) |
 
## Problemas comuns
 
- **Cards "sem dados ainda":** rode `curl.exe -X POST https://SUA-URL/api/refresh` e leia o campo `detail`. Pode ser instabilidade da API ou um `ano_modelo` inexistente (o erro lista os anos válidos).
- **Banco "Powered off":** serviços de plano pago da Aiven desligam quando o teste termina. Use o plano gratuito e atualize `AIVEN_DB_URL`.
- **Build falha com Dockerfile não encontrado:** o `Dockerfile` precisa estar na raiz do repositório.
- **CI falha com `repository name must be lowercase`:** o GHCR exige nome de imagem em minúsculas; o `ci-cd.yml` converte `GITHUB_REPOSITORY` antes do build.
- **`invalid dsn`:** o `AIVEN_DB_URL` foi colado incompleto; use o botão de copiar da Aiven.
- **502 com o app "live":** defina `PORT=8080` manualmente no Render.
 
