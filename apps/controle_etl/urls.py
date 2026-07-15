"""Rotas da API de controle do ETL de identidades."""

from django.urls import path

from . import views

urlpatterns = [
    # Execuções
    path(
        "etl/execucoes/",
        views.ExecucoesView.as_view(),
        name="etl-execucoes",
    ),
    path(
        "etl/execucoes/<int:pk>/",
        views.DetalheExecucaoView.as_view(),
        name="etl-execucao-detalhe",
    ),
    path(
        "etl/execucoes/<int:pk>/cancelar/",
        views.CancelarExecucaoView.as_view(),
        name="etl-execucao-cancelar",
    ),
    # Controle de provisionamento
    path(
        "etl/provisionamento/",
        views.ControleProvisionamentoView.as_view(),
        name="etl-provisionamento",
    ),
    # Watermark
    path(
        "etl/watermark/",
        views.MarcaDaguaView.as_view(),
        name="etl-watermark",
    ),
    path(
        "etl/watermark/<str:fonte>/resetar/",
        views.ResetarMarcaDaguaView.as_view(),
        name="etl-watermark-resetar",
    ),
    # Checkpoints
    path(
        "etl/checkpoints/",
        views.CheckpointsView.as_view(),
        name="etl-checkpoints",
    ),
    # Rastreio de tentativas
    path(
        "etl/tentativas/",
        views.TentativasView.as_view(),
        name="etl-tentativas",
    ),
    # Estatísticas
    path(
        "etl/estatisticas/",
        views.estatisticas,
        name="etl-estatisticas",
    ),
    # Consulta de identidades (cpf/rf)
    path(
        "etl/identidades/consultar/",
        views.ConsultaIdentidadeView.as_view(),
        name="etl-identidades-consultar",
    ),
    # Monitoramento público
    path(
        "etl/monitoramento/resumo/",
        views.ResumoExecucoesView.as_view(),
        name="etl-monitoramento-resumo",
    ),
    # Health check
    path(
        "etl/health/",
        views.HealthCheckView.as_view(),
        name="etl-health",
    ),
    # Sistemas e perfis CoreSSO → Keycloak
    path(
        "etl/sistemas/extrair/",
        views.extrair_sistemas,
        name="etl-sistemas-extrair",
    ),
    path(
        "etl/sistemas/provisionar/",
        views.provisionar_sistemas,
        name="etl-sistemas-provisionar",
    ),
    path(
        "etl/sistemas/",
        views.listar_sistemas,
        name="etl-sistemas",
    ),
    path(
        "etl/perfis/extrair/",
        views.extrair_perfis,
        name="etl-perfis-extrair",
    ),
    path(
        "etl/perfis/provisionar/",
        views.provisionar_perfis,
        name="etl-perfis-provisionar",
    ),
    path(
        "etl/perfis/",
        views.listar_perfis,
        name="etl-perfis",
    ),
    # Vínculos usuário↔grupo → client roles Keycloak
    path(
        "etl/vinculos/extrair/",
        views.extrair_vinculos,
        name="etl-vinculos-extrair",
    ),
    path(
        "etl/vinculos/provisionar/",
        views.provisionar_vinculos,
        name="etl-vinculos-provisionar",
    ),
    path(
        "etl/vinculos/",
        views.listar_vinculos,
        name="etl-vinculos",
    ),
    # Criação manual de usuário (sem vínculo prévio no CoreSSO)
    path(
        "etl/usuario/criar/",
        views.criar_usuario_manual,
        name="etl-usuario-criar",
    ),
    # Sincronização individual de usuário
    path(
        "etl/usuario/sincronizar/",
        views.sincronizar_usuario,
        name="etl-usuario-sincronizar",
    ),
    # Concessão de acesso a sistema + roles
    path(
        "etl/usuario/conceder-acesso/",
        views.conceder_acesso,
        name="etl-usuario-conceder-acesso",
    ),
    # Pipeline completo por sistema
    path(
        "etl/pipeline-sistema/",
        views.executar_pipeline_sistema,
        name="etl-pipeline-sistema",
    ),
]
