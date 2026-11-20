"""Consultas SQL do domínio de sistemas."""

FILTRO_EXCLUIR_SISTEMAS_CORESSO = "WHERE sis_id NOT IN ({ids})"

SISTEMAS_CORESSO = """
        SELECT
            sis_id, sis_nome, sis_descricao,
            sis_tipoAutenticacao, sis_urlIntegracao,
            sis_caminhoLogout, sis_situacao
        FROM SYS_Sistema {filtro_exclusao} ORDER BY sis_id
    """

PERFIS_CORESSO = """
        SELECT gru_id, gru_nome, sis_id, vis_id, gru_situacao
        FROM SYS_Grupo {filtro_exclusao} ORDER BY sis_id, gru_id
    """

CONFIGURACOES_RECUPERACAO_SENHA_EOL_APOIO = (
    "SELECT codigo_sistema, nome_sistema, pagina_recuperacao_senha "
    "FROM public.sistemarecuperacaosenha ORDER BY codigo_sistema"
)
