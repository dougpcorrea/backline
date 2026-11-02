#!/usr/bin/env bash
# Instala o bot do Backline como um usuário comum (sem sudo), por exemplo o usuário "painel"
# do servidor do Painel. Os segredos vêm da tabela backline_segredos do Supabase,
# lida com a chave que o servidor já tem em /etc/painel-nuvem/config.json.
#
# Uso (de dentro da pasta do projeto):  bash deploy/install-sem-root.sh
# Rodar de novo atualiza o código e os segredos, e reinicia o bot.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/backline"
CFG=/etc/painel-nuvem/config.json

echo "==> Copiando o código para $APP"
mkdir -p "$APP"
if [[ "$SRC" != "$APP" ]]; then
  cp -r "$SRC"/. "$APP"/
fi
cd "$APP"

echo "==> Python e dependências"
export PATH="$HOME/.local/bin:$PATH"
if ! python3 -m pip --version >/dev/null 2>&1; then
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
  python3 /tmp/get-pip.py --user --break-system-packages -q
fi
python3 -m pip install --user --break-system-packages -q -r requirements.txt

echo "==> Segredos (Supabase > backline_segredos)"
python3 - "$CFG" "$APP/.env" <<'PY'
import json, sys, urllib.request
cfg_path, env_path = sys.argv[1:]
cfg = json.load(open(cfg_path))
url, key = cfg["supabaseUrl"].rstrip("/"), cfg["supabaseChave"]
req = urllib.request.Request(f"{url}/rest/v1/backline_segredos?select=nome,valor",
                             headers={"apikey": key, "Authorization": f"Bearer {key}"})
rows = {r["nome"]: (r["valor"] or "").strip() for r in json.load(urllib.request.urlopen(req))}
missing = [k for k, v in rows.items() if not v]
lines = open(".env.example").read().splitlines()
out = []
for line in lines:
    k = line.split("=", 1)[0]
    out.append(f"{k}={rows[k]}" if k in rows and rows[k] else line)
open(env_path, "w").write("\n".join(out) + "\n")
print("   ok:", ", ".join(k for k, v in rows.items() if v) or "nenhum")
if missing:
    print("   FALTANDO:", ", ".join(missing))
PY
chmod 600 "$APP/.env"

echo "==> Início automático (cron)"
cat > "$APP/rodar.sh" <<'SH'
#!/usr/bin/env bash
# Liga o bot se ele não estiver rodando (chamado pelo cron a cada 2 min e no boot).
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
pgrep -u "$(id -u)" -f "python3 -m backline.bot" >/dev/null && exit 0
mkdir -p log
nohup python3 -m backline.bot >> log/bot.log 2>&1 &
SH
chmod +x "$APP/rodar.sh"
( { crontab -l 2>/dev/null || true; } | grep -v "backline/rodar.sh" || true ;
  echo "@reboot $APP/rodar.sh" ;
  echo "*/2 * * * * $APP/rodar.sh" ) | crontab -

echo "==> (Re)iniciando o bot"
pkill -u "$(id -u)" -f "python3 -m backline.bot" || true
sleep 2
"$APP/rodar.sh"
sleep 12
if grep -q "Conectado como" "$APP/log/bot.log" 2>/dev/null; then
  echo "✅ Backline rodando."
else
  echo "⚠️  Últimas linhas do log:"; tail -n 25 "$APP/log/bot.log" 2>/dev/null || true
fi
echo "Log: tail -f $APP/log/bot.log"
