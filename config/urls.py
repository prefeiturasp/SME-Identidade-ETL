"""Configuração de URLs do SME-Identidade-ETL.

Expõe o dashboard/kanban HTML (sem autenticação), o schema e a UI do
Swagger (públicos) e a API v1 (`apps.controle_etl.urls`), autenticada
por API Key.
"""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.permissions import AllowAny

from apps.controle_etl.views import (
    DashboardView,
    DispararExecucaoDashboardView,
    KanbanView,
)

_API_V1 = "identidade-etl/api/v1/"

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("dashboard/kanban/", KanbanView.as_view(), name="kanban"),
    path(
        "dashboard/executar/",
        DispararExecucaoDashboardView.as_view(),
        name="dashboard-executar",
    ),
    path(
        f"{_API_V1}schema/",
        SpectacularAPIView.as_view(
            authentication_classes=[],
            permission_classes=[AllowAny],
        ),
        name="schema",
    ),
    path(
        f"{_API_V1}docs/",
        SpectacularSwaggerView.as_view(
            url_name="schema",
            authentication_classes=[],
            permission_classes=[AllowAny],
        ),
        name="swagger-ui",
    ),
    path(_API_V1, include("apps.controle_etl.urls")),
]
