# Dashboard de Preços FIPE — Pipeline DevOps
 
Dashboard Flask que acompanha a evolução do preço de veículos na **Tabela FIPE**,
com os dados guardados em um **PostgreSQL gerenciado pela Aiven.io**. Um pipeline
de CI/CD no **GitHub Actions** testa o código, constrói a imagem Docker, publica no
GitHub Container Registry (GHCR) e dispara o deploy no **Render**.
 
Este projeto é uma adaptação do laboratório original (que usava dados do Banco
Central) para outra fonte de dados abertos: preços de veículos da Tabela FIPE.
 
## O que o projeto mostra
 
- Um card por veículo com o preço mais recente e a variação em relação ao mês anterior.
- Gráfico com a evolução do preço dos últimos meses.
- Total de acessos e tempo de atividade do app, lidos do PostgreSQL.
## Tecnologias
 
Python/Flask · PostgreSQL (Aiven) · Docker · GitHub Actions · GHCR · Render ·
API de dados abertos da FIPE (com a BrasilAPI como reserva)
 
## Estrutura do repositório
 
```
.
├── Dockerfile               # Builda a partir da raiz do repo, copia de app/
├── app/
│   ├── app.py               # Rotas Flask, cache e montagem do dashboard
│   ├── fipe_client.py       # Cliente das APIs de FIPE + acesso ao PostgreSQL
│   ├── requirements.txt
│   ├── templates/           # dashboard.html
│   └── static/              # style.css
└── .github/workflows/
    ├── ci-cd.yml            # Testa, builda, publica e faz deploy
    └── refresh-data.yml     # Atualiza os preços periodicamente (cron)
```
 
> O `Dockerfile` fica na **raiz** do repositório, mesmo com a aplicação em `app/`.
> Assim o contexto de build padrão (raiz) já é o correto — ver Troubleshooting.
 
## Fonte de dados: Tabela FIPE
 
O app consulta duas APIs públicas, nesta ordem (se a primeira falhar, usa a segunda):
 
1. **API v2 da FIPE** (`fipe.parallelum.com.br` / `fipe.api.br`) — sem cadastro,
   500 consultas por dia (1.000 com um token gratuito).
2. **BrasilAPI** (`brasilapi.com.br/api/fipe`) — sem chave.
Para cada veículo, o app busca o histórico de preços (ou o preço de cada mês de
referência, se o histórico não estiver disponível) e grava em `fipe_precos`.
 
### Escolhendo os veículos
 
Edite o dicionário `SERIES` em `app/fipe_client.py`. A chave é o **código FIPE**
do modelo (ex.: `001494-0`), que aparece em sites como tabelafipebrasil.com:
 
```python
SERIES = {
    "001494-0": {"name": "ARGO DRIVE 1.0 6V Flex", "ano_modelo": None, "unit": "R$", "casas": 2},
}
```
 
| Campo | Significado |
|---|---|
| `name` | Nome no card (`None` = usa marca/modelo/ano vindos da API) |
| `ano_modelo` | Ano do modelo a acompanhar (`None` = o mais novo disponível) |
| `unit`, `casas` | Unidade e casas decimais do valor |
 
Se o `ano_modelo` não existir para o código, o erro lista os anos disponíveis.
 
### Como funciona o cache
 
1. Cada preço mensal fica na tabela `fipe_precos` (`codigo_fipe`, `ano_modelo`,
   `ref_date`, `valor`, `fetched_at`).
2. A cada visita, o app confere se o cache de cada veículo tem mais de
   `CACHE_HOURS` (padrão: 24h). Se sim, busca dados novos e faz `UPSERT`.
3. Se as APIs estiverem fora do ar, o app **não quebra**: continua mostrando o
   que já está no banco e registra um aviso no log.
4. O workflow `refresh-data.yml` chama `POST /api/refresh` por cron, mantendo o
   cache atualizado mesmo sem visitas.
A Tabela FIPE muda uma vez por mês, então o gráfico ganha um novo ponto por mês.
 
## Passo a passo (tudo pelo navegador)
 
### 1. Banco de dados na Aiven
- Crie uma conta na [Aiven](https://aiven.io) e um serviço **PostgreSQL**.
- Em **Connection information**, copie o **Service URI** (`postgres://...`).
- Para uso contínuo, prefira o **plano gratuito** do PostgreSQL: serviços de plano
  pago são desligados quando o período de teste termina (ver Troubleshooting).
### 2. Deploy no Render
- **New → Web Service → Build and deploy from a Git repository**, aponte para este repositório.
- Em **Settings**:
  - **Root Directory**: vazio
  - **Dockerfile Path**: `./Dockerfile`
  - **Docker Build Context Directory**: `.`
- Em **Environment**, adicione `AIVEN_DB_URL` (o Service URI) e `PORT` = `8080`.
- Em **Settings → Deploy Hook**, copie a URL (vai para um secret do GitHub).
### 3. Secrets do GitHub
Em **Settings → Secrets and variables → Actions**:
 
| Secret | Valor |
|---|---|
| `RENDER_DEPLOY_HOOK_URL` | URL do Deploy Hook do Render |
| `APP_HEALTHCHECK_URL` | URL pública do app, **sem barra no final** (ex.: `https://meu-app.onrender.com`) |
 
### 4. Pipeline de CI/CD
Um `push` em `app/` ou no `Dockerfile`, na branch `main`, dispara o workflow
**CI/CD - Dashboard App**:
 
1. **Testes e lint** — instala dependências, roda flake8 e checa a sintaxe (`py_compile`).
2. **Build + push** — constrói a imagem e publica em `ghcr.io/<usuario>/<repo>/dashboard-app`.
3. **Deploy (Render)** — chama o Deploy Hook e verifica `/healthz`.
Em pull requests, só o job de testes roda; build e deploy acontecem no `push` na `main`.
 
### 5. Acessar o dashboard
Abra a URL pública do Render. No plano gratuito o serviço "dorme" por inatividade,
então o primeiro acesso pode levar cerca de 1 minuto, e a primeira visita ainda
precisa buscar os preços nas APIs.
 
## Endpoints
 
| Rota | Descrição |
|---|---|
| `GET /` | Dashboard |
| `GET /api/indicators` | Dados do dashboard em JSON |
| `POST /api/refresh` | Força a atualização dos preços (mostra o erro no campo `detail`, se falhar) |
| `GET /healthz` | Health check (testa a conexão com o banco) |
 
## Variáveis de ambiente
 
| Variável | Obrigatória? | Descrição |
|---|---|---|
| `AIVEN_DB_URL` | Sim | Service URI do PostgreSQL |
| `PORT` | Sim, no Render | `8080` — precisa bater com o `--bind` do `CMD` no Dockerfile |
| `CACHE_HOURS` | Não | Horas antes de buscar preços novos (padrão: 24) |
| `FIPE_TOKEN` | Não | Token gratuito de fipe.api.br; dobra o limite diário de consultas |
 
## Troubleshooting — problemas que apareceram no projeto
 
### 1. Dashboard abre, mas os cards ficam "sem dados ainda"
A busca nas APIs falhou, e o app só registra o aviso no log. Para ver o motivo:
 
```
curl.exe -X POST https://SUA-URL.onrender.com/api/refresh
```
 
O campo `detail` da resposta mostra o erro de cada fonte. Causas vistas:
- **BrasilAPI com erro 500** (`/api/fipe/tabelas/v1`): é instabilidade da API, não do código. Por isso a API v2 da FIPE é a fonte principal.
- **`ano_modelo` inexistente**: o erro lista os anos válidos; troque ou use `None`.
### 2. Banco "Powered off" e app em loop (`could not translate host name`)
O PostgreSQL de plano pago foi desligado quando o período de teste da Aiven terminou
(aviso "Your platform trial has ended"). Solução: usar o serviço do **plano gratuito**,
copiar o novo Service URI e atualizar `AIVEN_DB_URL` no Render.
 
### 3. Build falha: `failed to read dockerfile` ou `"/app": not found`
O `Dockerfile` usa `COPY app/...`, então precisa estar na **raiz** do repo, com
Root Directory vazio, Dockerfile Path `./Dockerfile` e Build Context `.`.
Se estiver em `app/`, o caminho e o contexto entram em conflito.
 
### 4. CI falha: `repository name must be lowercase`
O GHCR só aceita nomes de imagem em minúsculas, e o nome de usuário do GitHub pode
ter maiúsculas. O workflow converte antes do build:
 
```yaml
- run: echo "IMAGE=ghcr.io/${GITHUB_REPOSITORY,,}/dashboard-app" >> "$GITHUB_ENV"
```
 
### 5. CI falha em "Checagem de sintaxe": `No such file or directory: 'bcb_client.py'`
O workflow ainda apontava para o arquivo antigo. Ao trocar de fonte de dados,
atualize também o `py_compile` no `ci-cd.yml` (`app.py fipe_client.py`).
Um "Re-run jobs" usa o workflow do commit antigo; faça um commit novo.
 
### 6. `invalid dsn` / `invalid connection option`
O `AIVEN_DB_URL` foi colado incompleto ou com espaços. Copie pelo botão de copiar
da Aiven; o valor deve começar com `postgres://` e ficar em uma linha só.
 
### 7. Render mostra "live", mas o navegador dá 502 ou carrega para sempre
Crie manualmente a variável `PORT` com o mesmo valor do `--bind` do Dockerfile (`8080`).
 
### 8. `TemplateNotFound`
O Flask procura a pasta `templates` (com "e"). Confira o nome exato no GitHub.
 
### 9. Health check do deploy falha na primeira tentativa
O plano gratuito do Render demora a acordar. O job usa `curl --retry` para tentar
novamente por alguns minutos.
 
### Checklist rápido se o deploy não sobe
1. `Dockerfile` na raiz e Build Context `.`?
2. Nomes de arquivo em `app/` sem espaços acidentais?
3. `AIVEN_DB_URL` preenchida e o PostgreSQL **Running** na Aiven?
4. `PORT=8080` preenchida no Render?
5. O IP allowlist do PostgreSQL permite as conexões do Render (`0.0.0.0/0` em estudo)?
## Possíveis extensões
- Comparar dois modelos no mesmo gráfico ou mostrar a variação percentual no período.
- Alertar quando o preço de um modelo cair mais de uma certa porcentagem.
- Reativar a ingestão por streaming (Kafka) e um painel no Grafana, se o plano permitir.
 
