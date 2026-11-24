#!/usr/bin/env bash
# Muestra la dirección pública actual del túnel. Se corre en el servidor:
#
#     bash ~/asistente/despliegue/direccion_tunel.sh
#
# O desde la Mac:
#
#     ssh -i ~/.ssh/asistente-soporte.pem ubuntu@<ip> bash asistente/despliegue/direccion_tunel.sh
set -euo pipefail

host=$(curl -fsS http://127.0.0.1:20241/quicktunnel | python3 -c 'import json, sys; print(json.load(sys.stdin)["hostname"])')
echo "https://$host"
