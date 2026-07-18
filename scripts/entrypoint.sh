#!/bin/sh
# Aplica migrations antes de subir o servidor, sem bloquear o boot
# caso a migration falhe (ex.: banco indisponível momentaneamente).
set -e

python manage.py migrate --noinput || echo "AVISO: migrate falhou, subindo mesmo assim"

exec "$@"
