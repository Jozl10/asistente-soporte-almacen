#!/usr/bin/env bash
# Sube el código y los modelos a la EC2 y reinicia la API. Se corre desde la Mac, en codigo/:
#
#     despliegue/subir.sh ubuntu@<ip-de-la-ec2>
#
# No sube los datos privados (tickets y capturas reales), la bitácora ni el entorno de Python.
# Tampoco reinicia el túnel: si se reinicia, cambia su dirección.
set -euo pipefail

DESTINO="${1:?Uso: despliegue/subir.sh ubuntu@<ip-de-la-ec2>}"
LLAVE="${LLAVE:-$HOME/.ssh/asistente-soporte.pem}"
cd "$(dirname "$0")/.."

rsync -az --delete -e "ssh -i $LLAVE" \
  --exclude datos-privados/ --exclude bitacora/ --exclude venv/ --exclude .venv/ \
  --exclude __pycache__/ --exclude .pytest_cache/ --exclude .DS_Store \
  ./ "$DESTINO:asistente/"

# La primera vez el servicio todavía no existe: lo crea preparar_servidor.sh.
ssh -i "$LLAVE" "$DESTINO" \
  'if [ -f /etc/systemd/system/asistente-api.service ]; then
     sudo systemctl restart asistente-api && echo "API reiniciada"
   fi'
