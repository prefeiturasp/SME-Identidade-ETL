"""Roteamento de URLs para os endpoints da API de staging."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"servidores", views.StagingUsuarioServidorViewSet, basename="staging-servidor")
router.register(r"alunos", views.StagingUsuarioAlunoViewSet, basename="staging-aluno")
router.register(r"terceiros", views.StagingUsuarioTerceiroViewSet, basename="staging-terceiro")
router.register(r"perfis", views.StagingPerfilViewSet, basename="staging-perfil")
router.register(r"lotacoes", views.StagingLotacaoViewSet, basename="staging-lotacao")
router.register(r"dedup", views.DedupResultViewSet, basename="staging-dedup")

urlpatterns = [
    path("", include(router.urls)),
]
