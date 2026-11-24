#!/usr/bin/env bash
# Prepara la EC2 (Ubuntu 24.04, t3.small) para la API. Se corre una sola vez, en el servidor,
# después de subir el código y los modelos con despliegue/subir.sh:
#
#     bash ~/asistente/despliegue/preparar_servidor.sh
#
# Deja dos servicios que arrancan solos al prender la máquina:
#   asistente-api      la API de Flask con gunicorn, solo en 127.0.0.1:5000
#   tunel-cloudflare   el túnel rápido de Cloudflare, que le da a la API una dirección HTTPS
#                      pública sin abrir puertos ni tener dominio
set -euo pipefail

CARPETA="$(cd "$(dirname "$0")/.." && pwd)"

# 1. Memoria de intercambio de 2 GB. TensorFlow cabe en los 2 GB de la t3.small, pero con
#    poco margen.
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# 2. Python, Tesseract (el OCR) y cloudflared (del repositorio oficial de Cloudflare).
sudo apt-get update -q
sudo apt-get install -y -q python3-venv tesseract-ocr
if ! command -v cloudflared >/dev/null; then
  sudo mkdir -p --mode=0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
    | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
    | sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
  sudo apt-get update -q
  sudo apt-get install -y -q cloudflared
fi

# 3. Entorno de Python con las mismas versiones que en la Mac.
python3 -m venv "$CARPETA/venv"
"$CARPETA/venv/bin/pip" install -q --upgrade pip
"$CARPETA/venv/bin/pip" install -q -r "$CARPETA/requirements.txt"

# 4. Modelo de español de Tesseract, si no se subió con el código.
if [ ! -f "$CARPETA/tessdata/spa.traineddata" ]; then
  mkdir -p "$CARPETA/tessdata"
  curl -fsSL -o "$CARPETA/tessdata/spa.traineddata" \
    https://github.com/tesseract-ocr/tessdata_best/raw/main/spa.traineddata
fi

# 5. Variables de la API. Service Desk queda simulado para que nadie levante tickets reales
#    desde la URL pública. La clave de administración se genera aquí y no sale del servidor.
if [ ! -f /etc/asistente-api.env ]; then
  sudo install -m 600 -o "$USER" /dev/null /etc/asistente-api.env
  {
    echo "SERVICE_DESK_MODO=simulado"
    echo "PERMITIR_SIMULAR_FALLAS=1"
    echo "CLAVE_ADMIN=$(openssl rand -hex 24)"
  } > /etc/asistente-api.env
fi

# 6. Servicios.
sudo cp "$CARPETA/despliegue/asistente-api.service" "$CARPETA/despliegue/tunel-cloudflare.service" \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now asistente-api tunel-cloudflare
echo "Listo. Dirección del túnel: bash $CARPETA/despliegue/direccion_tunel.sh"
