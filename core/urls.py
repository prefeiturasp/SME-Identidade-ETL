"""Roteamento de URLs para os endpoints da API do core do ETL."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"executions", views.ETLExecutionViewSet, basename="etl-execution")
router.register(
    r"upsert-control",
    views.UpsertControlViewSet,
    basename="upsert-control"
)

urlpatterns = [
    path("", include(router.urls)),
    path("stats/", views.etl_stats, name="etl-stats"),
    path("test-kc-upsert/", views.test_kc_upsert, name="etl-test-kc-upsert"),
    path(
        "test-kc-upsert/<str:cpf>/",
        views.test_kc_upsert,
        name="etl-test-kc-upsert-cpf"
    ),
    path("sistemas/extract/", views.sistemas_extract, name="sistemas-extract"),
    path(
        "sistemas/load-keycloak/",
        views.sistemas_load_keycloak,
        name="sistemas-load-kc"
    ),
    path("sistemas/", views.sistemas_list, name="sistemas-list"),
    path("perfis/extract/", views.perfis_extract, name="perfis-extract"),
    path("perfis/load-keycloak/", views.perfis_load_keycloak, name="perfis-load-kc"),
    path("perfis/", views.perfis_list, name="perfis-list"),
    path("retroalim/", views.retroalim_list, name="retroalim-list"),
]
