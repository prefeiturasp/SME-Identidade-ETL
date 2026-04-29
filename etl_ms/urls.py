"""
URL configuration for ETL-MS.

Rotas:
  /api/health/       — health check
  /api/etl/          — CRUD de execuções ETL, status, logs
  /api/staging/      — consulta dados de staging
  /admin/            — Django admin
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", include("health_check.urls")),
    path("api/etl/", include("core.urls")),
    path("api/staging/", include("staging.urls")),
    # OpenAPI schema + Swagger UI + ReDoc
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
