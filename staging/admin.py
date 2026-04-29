from django.contrib import admin

from .models import StagingLotacao, StagingPerfil

@admin.register(StagingPerfil)
class StagingPerfilAdmin(admin.ModelAdmin):
    list_display = [
        "cargo_codigo",
        "cargo_nome",
        "keycloak_role",
        "is_active",
    ]
    list_filter = ["keycloak_role", "is_active"]


@admin.register(StagingLotacao)
class StagingLotacaoAdmin(admin.ModelAdmin):
    list_display = [
        "codigo",
        "nome",
        "tipo",
        "dre_codigo",
        "keycloak_group_path",
        "is_active",
    ]
    list_filter = ["tipo", "is_active"]
    search_fields = ["codigo", "nome"]
