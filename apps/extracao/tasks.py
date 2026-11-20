"""Entradas de extração preservadas para os consumidores do ETL.

As consultas e o controle de watermark ficam em services.
"""

from apps.extracao.dtos import (
    RegistroIdentidade,
    VinculoServidor,
)
from apps.extracao.services.alunos import (
    extrair_eol_alunos,
)
from apps.extracao.services.sistemas import (
    extrair_perfis_coresso,
    extrair_sistemas_coresso,
    extrair_sistemas_recuperacao,
)
from apps.extracao.services.usuarios import (
    _extrair_coresso_por_identificadores,
    buscar_complemento_se1426,
    buscar_dados_usuario_coresso,
    buscar_grupos_coresso_por_login,
    buscar_permissoes_usuario_coresso,
    extrair_coresso,
    extrair_vinculos_se1426,
    extrair_vinculos_usuario_grupo_coresso,
)

__all__ = [
    "RegistroIdentidade",
    "VinculoServidor",
    "_extrair_coresso_por_identificadores",
    "buscar_dados_usuario_coresso",
    "buscar_grupos_coresso_por_login",
    "buscar_permissoes_usuario_coresso",
    "extrair_coresso",
    "extrair_perfis_coresso",
    "extrair_sistemas_coresso",
    "extrair_vinculos_usuario_grupo_coresso",
    "extrair_eol_alunos",
    "extrair_sistemas_recuperacao",
    "buscar_complemento_se1426",
    "extrair_vinculos_se1426",
]
