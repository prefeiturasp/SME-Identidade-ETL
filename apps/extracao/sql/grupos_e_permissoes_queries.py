"""Consultas SQL de grupos, perfis e permissões."""

FILTRO_EXCLUIR_SISTEMAS_USUARIO_CORESSO = "AND g.sis_id NOT IN ({ids})"


FILTRO_SISTEMA_USUARIO_CORESSO = "AND g.sis_id = {sis_id}"


FILTRO_GRUPO_USUARIO_CORESSO = "AND g.gru_id = '{gru_id}'"


GRUPOS_USUARIO_CORESSO = """
        SELECT
            g.gru_id    AS gru_id,
            g.gru_nome  AS nome
        FROM SYS_Grupo g
        INNER JOIN SYS_UsuarioGrupo ug ON ug.gru_id = g.gru_id
        INNER JOIN SYS_Usuario u ON u.usu_id = ug.usu_id
        WHERE u.usu_login = ?
          AND g.gru_situacao = 1
    """


VINCULOS_USUARIO_GRUPO_CORESSO = """
        SELECT
            u.usu_login     AS login,
            doc.psd_numero  AS cpf,
            pes.pes_nome    AS nome,
            u.usu_email     AS email,
            g.gru_id        AS gru_id,
            g.gru_nome      AS gru_nome,
            g.sis_id        AS sis_id
        FROM SYS_Usuario u
        INNER JOIN PES_Pessoa pes
            ON pes.pes_id = u.pes_id
        LEFT JOIN PES_PessoaDocumento doc
            ON doc.pes_id = pes.pes_id
            AND doc.tdo_id = (
                SELECT TOP 1 tdo_id
                FROM SYS_TipoDocumentacao
                WHERE tdo_nome LIKE '%CPF%'
            )
        INNER JOIN SYS_UsuarioGrupo ug
            ON ug.usu_id = u.usu_id
        INNER JOIN SYS_Grupo g
            ON g.gru_id = ug.gru_id
        WHERE g.gru_situacao = 1
          AND u.usu_situacao = 1
          {clausulas}
        ORDER BY u.usu_login, g.sis_id
    """


PERMISSOES_USUARIO_CORESSO = """
        SELECT
            gp.sis_id          AS sis_id,
            s.sis_nome         AS sis_nome,
            gp.mod_id          AS mod_id,
            m.mod_nome         AS mod_nome,
            gp.grp_consultar   AS consultar,
            gp.grp_inserir     AS inserir,
            gp.grp_alterar     AS alterar,
            gp.grp_excluir     AS excluir
        FROM SYS_GrupoPermissao gp
        LEFT JOIN SYS_Sistema s
            ON s.sis_id = gp.sis_id
        LEFT JOIN SYS_Modulo m
            ON m.sis_id = gp.sis_id AND m.mod_id = gp.mod_id
        WHERE gp.gru_id IN ({marcadores})
          AND (
              gp.grp_consultar = 1 OR gp.grp_inserir = 1
              OR gp.grp_alterar = 1 OR gp.grp_excluir = 1
          )
    """


PERFIS_CORESSO = """
        SELECT gru_id, gru_nome, sis_id, vis_id, gru_situacao
        FROM SYS_Grupo {filtro_exclusao} ORDER BY sis_id, gru_id
    """

FILTRO_EXCLUIR_SISTEMAS_CORESSO = "WHERE sis_id NOT IN ({ids})"
