"""WSGI config for ETL-MS."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "etl_ms.settings")

application = get_wsgi_application()
