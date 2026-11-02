#!/usr/bin/env bash
# Instala (ou atualiza) o bot do Backline num servidor Ubuntu/Debian.
# Uso, de dentro da pasta do projeto no servidor:
#   sudo bash deploy/install.sh
# Na primeira vez ele pergunta as chaves (o que tu cola não aparece na tela).
set -euo pipefail

APP_DIR=/opt/backline
APP_USER=backline
SERVICE=backline
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Rode com sudo."; exit 1; fi

echo "==> Pacotes do sistema"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip rsync >/dev/null

echo "==> Usuário e pasta"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR"
rsync -a --delete --exclude ".env" --exclude ".venv" --exclude "*.db*" --exclude "__pycache__" \
      "$SRC_DIR"/ "$APP_DIR"/

echo "==> Ambiente Python"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

ENV_FILE="$APP_DIR/.env"
ask() {  # ask VAR "pergunta" [secreto]
  local var=$1 prompt=$2 secret=${3:-} value=""
  if grep -q "^$var=..*" "$ENV_FILE" 2>/dev/null; then return; fi
  while [[ -z "$value" ]]; do
    if [[ -n "$secret" ]]; then read -r -s -p "$prompt: " value; echo; else read -r -p "$prompt: " value; fi
  done
  if grep -q "^$var=" "$ENV_FILE"; then
    python3 - "$ENV_FILE" "$var" "$value" <<'PY'
import sys; p, k, v = sys.argv[1:]
lines = open(p).read().splitlines()
open(p, "w").write("\n".join(f"{k}={v}" if l.startswith(k + "=") else l for l in lines) + "\n")
PY
  else
    echo "$var=$value" >> "$ENV_FILE"
  fi
}

echo "==> Configuração (.env)"
[[ -f "$ENV_FILE" ]] || cp "$APP_DIR/.env.example" "$ENV_FILE"
ask GUILD_ID         "ID do servidor do Discord"
ask DISCORD_TOKEN    "Token do bot do Discord (Developer Portal > Bot > Reset Token)" secret
ask YOUTUBE_API_KEY  "Chave da API do YouTube (Google Cloud > Credenciais > Backline bot)" secret
ask APIFY_TOKEN      "Token da Apify (Settings > API & Integrations)" secret
ask DATABASE_URL     "Conexão do Supabase (Connect > Session pooler, com a senha)" secret
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
chmod 600 "$ENV_FILE"

echo "==> Serviço (liga sozinho e reinicia se cair)"
cat > /etc/systemd/system/$SERVICE.service <<UNIT
[Unit]
Description=Backline Discord bot
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m backline.bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now $SERVICE >/dev/null
systemctl restart $SERVICE
sleep 8
if systemctl is-active --quiet $SERVICE && journalctl -u $SERVICE -n 50 --no-pager | grep -q "Conectado como"; then
  echo "✅ Backline rodando."
else
  echo "⚠️  Verifique o log abaixo:"
  journalctl -u $SERVICE -n 30 --no-pager
fi
echo "Log ao vivo: journalctl -u $SERVICE -f"
