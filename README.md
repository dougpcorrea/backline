# Backline Bot

Bot do Discord da comunidade Backline. Um bot só, com módulos independentes que compartilham o mesmo banco de dados.

| Módulo | Arquivo | O que faz |
|---|---|---|
| Setup | `cogs/server_setup.py` | `/setup` cria cargos, canais e painéis (pode rodar de novo quando quiser) |
| Entrada | `cogs/onboarding.py` | Botão **Entrar** → @ das plataformas → gêneros → código na bio → **Verificar** |
| Feed | `cogs/feed.py` | Botão **Nova publicação** / `/post` → cartão no `#feed` com contador de comentários |
| Participação | `cogs/participation.py` | Coleta comentários, calcula o índice, atualiza cargos Lv.1–10, ranking e `/profile` |
| Recrutamento | `cogs/recruitment.py` | `/invite` gera link pessoal; o bot registra quem entrou por qual link |
| Admin | `cogs/admin.py` | `/admin status, pause, resume, recalc, collect, remove-post, member, verify, unverify` |

A lógica de pontuação fica separada do Discord (`scoring.py` e `services.py`) e tem testes em `tests/`.

---

## Como funciona o índice

- Janela móvel de **30 dias** (pela data da publicação).
- **Dados:** quantas publicações de outros membros a pessoa comentou (vários comentários no mesmo post contam 1).
- **Recebidos:** quantos membros diferentes comentaram em cada publicação dela, somados.
- **Nível** = (dados ÷ recebidos) × 10, de 1 a 10. Dar tanto quanto recebe = Lv.10.
- Quem deu menos de **5** comentários na janela fica no máximo no Lv.3.
- Comentários de quem não é membro verificado não contam. Responder no próprio post também não.
- A coleta de cada publicação acontece **24 h, 3 dias e 7 dias** depois de publicada. Os cargos são recalculados após cada coleta e a cada 6 horas.

Divulgadores: recrutado verificado e ativo (5+ comentários na janela) vale 1 ponto; verificado mas inativo vale 0,25; quem nunca verificou vale 0. Com **3** recrutados ativos a pessoa ganha o cargo 🛡️ Divulgador e acesso ao `#recruiters-lounge`.

Todos esses números ficam no `.env` e podem ser ajustados sem mexer no código.

---

## Instalação

### 1. Criar o bot no Discord
1. Acesse https://discord.com/developers/applications → **New Application** → nome "Backline".
2. Aba **Bot** → **Reset Token** → copie o token (vai no `.env`).
3. Na mesma aba, ligue **Server Members Intent**.
4. Aba **OAuth2 → URL Generator**: marque `bot` e `applications.commands`; em permissões marque **Administrator**. Abra o link gerado e adicione o bot ao servidor.
5. Em **Configurações do servidor → Cargos**, arraste o cargo do bot para o **topo** da lista.

### 2. Chaves de coleta
- **YouTube:** Google Cloud Console → crie um projeto → ative a *YouTube Data API v3* → **Credenciais → Criar chave de API**. A cota gratuita é suficiente pra começar.
- **Apify:** crie uma conta em apify.com → **Settings → Integrations** → copie o API token. A Apify cobra por resultado coletado; confira o preço na página de cada actor usado (estão no `.env.example`).

### 3. Rodar
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # e preencha o token, GUILD_ID e as chaves
python -m backline.bot
```
O bot precisa ficar ligado 24 horas. Pra isso, use um servidor pequeno (VPS, Railway, Fly.io etc.) com o mesmo comando.

### 4. Primeiro uso no Discord
1. Rode `/setup` (só administradores veem). Os canais, cargos e painéis são criados.
2. Faça a entrada você mesmo pelo `#start-here` pra testar a verificação.
3. Publique algo pelo `#post-here`, peça pra um amigo verificado comentar e rode `/admin collect` pra não esperar 24 h.
4. Confira `/profile` e `/admin status`.

---

## Primeiro teste com dados reais

Os actors da Apify às vezes mudam o nome dos campos que devolvem. O código já tenta vários nomes conhecidos, mas no primeiro teste confira:
- se a verificação do Instagram e do TikTok encontra o código na bio;
- se `/admin collect` encontra os comentários (o `/admin status` mostra erros de coleta).

Se algo falhar, o `/admin verify` libera um perfil manualmente enquanto o ajuste é feito.

## Observações
- A coleta de dados do Instagram e do TikTok por serviços como a Apify não é permitida pelos termos de uso dessas plataformas. As contas dos membros não participam da coleta, mas ela pode parar quando as plataformas mudarem algo. Use `/admin pause` para congelar os cargos enquanto resolve.
- No Instagram não dá pra confirmar pelo link se a publicação é mesmo do membro. No TikTok e no YouTube o bot confere.
- Testes: `pip install pytest pytest-asyncio && pytest`.

## Próximos módulos
- Avaliação de qualidade (envio, avaliação anônima por critérios, estrelas, proteção contra fraude)
- Mutual Follow
