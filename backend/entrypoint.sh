#!/bin/sh
set -eu
python manage.py migrate --noinput
# Tabela do cache compartilhado (limites de tentativa de login e de autorização).
python manage.py createcachetable
python manage.py collectstatic --noinput
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-3}" --timeout 60
