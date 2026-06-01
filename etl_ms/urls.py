"""Configuracao de URLs do ETL-MS.

Rotas:
    /api/etl/: CRUD de execucoes ETL, status e logs.
    /api/staging/: Consulta de dados de staging.
    /admin/: Django Admin.
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/etl/", include("core.urls")),
    path("api/staging/", include("staging.urls")),
    # OpenAPI schema + Swagger UI + ReDoc
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
]
