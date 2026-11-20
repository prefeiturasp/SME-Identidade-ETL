"""Consultas SQL do domínio de usuarios."""

FILTRO_SERVIDORES_ATIVOS_SE1426 = "WHERE s.situacao = 'Ativo'"
FILTRO_USUARIOS_ALTERADOS_CORESSO = " AND u.usu_dataAlteracao >= '{data}'"
FILTRO_USUARIOS_ATIVOS_CORESSO = " AND u.usu_situacao = 1"
FILTRO_IDENTIFICADORES_CORESSO = (
    " AND doc.psd_numero IN ({valores}) OR u.usu_login IN ({valores})"
)
FILTRO_EXCLUIR_SISTEMAS_USUARIO_CORESSO = "AND g.sis_id NOT IN ({ids})"
FILTRO_SISTEMA_USUARIO_CORESSO = "AND g.sis_id = {sis_id}"
FILTRO_GRUPO_USUARIO_CORESSO = "AND g.gru_id = '{gru_id}'"
FILTRO_EMAIL_CORESSO = "u.usu_email = ?"
FILTRO_CPF_CORESSO = "doc.psd_numero = ?"
FILTRO_LOGIN_CORESSO = "u.usu_login = ?"

VINCULOS_SERVIDOR_SE1426 = {
    "cargo_base": """
        SELECT
            sev.cd_registro_funcional     AS rf,
            CAST(cba.cd_cargo_base_servidor AS VARCHAR(50))
                                           AS codigo_vinculo_origem,
            CAST(cba.cd_cargo AS VARCHAR(50)) AS cargo_codigo,
            NULL                           AS cargo_nome,
            ls.cd_unidade_educacao        AS unidade_codigo,
            ue.nm_unidade_educacao        AS unidade_nome,
            ue.cd_unidade_administrativa_referencia AS dre_codigo,
            NULL                           AS situacao
        FROM v_servidor_cotic sev
        INNER JOIN v_cargo_base_cotic cba
            ON sev.cd_servidor = cba.cd_servidor
        INNER JOIN lotacao_servidor ls
            ON ls.cd_cargo_base_servidor = cba.cd_cargo_base_servidor
            AND ls.dt_fim IS NULL
        LEFT JOIN v_cadastro_unidade_educacao ue
            ON ue.cd_unidade_educacao = ls.cd_unidade_educacao
        WHERE cba.dt_fim_nomeacao IS NULL
            AND cba.dt_cancelamento IS NULL
    """,
    "cargo_sobreposto": """
        SELECT
            sev.cd_registro_funcional     AS rf,
            CAST(cba.cd_cargo_base_servidor AS VARCHAR(50))
                                           AS codigo_vinculo_origem,
            CAST(css.cd_cargo AS VARCHAR(50)) AS cargo_codigo,
            NULL                           AS cargo_nome,
            css.cd_unidade_local_servico   AS unidade_codigo,
            ue.nm_unidade_educacao         AS unidade_nome,
            ue.cd_unidade_administrativa_referencia AS dre_codigo,
            NULL                           AS situacao
        FROM v_servidor_cotic sev
        INNER JOIN v_cargo_base_cotic cba
            ON sev.cd_servidor = cba.cd_servidor
        INNER JOIN cargo_sobreposto_servidor css
            ON css.cd_cargo_base_servidor = cba.cd_cargo_base_servidor
            AND (css.dt_fim_cargo_sobreposto IS NULL
                 OR css.dt_fim_cargo_sobreposto > getdate())
        LEFT JOIN v_cadastro_unidade_educacao ue
            ON ue.cd_unidade_educacao = css.cd_unidade_local_servico
        WHERE cba.dt_fim_nomeacao IS NULL
            AND cba.dt_cancelamento IS NULL
    """,
    "funcao_atividade": """
        SELECT
            sev.cd_registro_funcional     AS rf,
            CONCAT(
                fat.cd_cargo_base_servidor, '-', fat.cd_tipo_funcao, '-',
                fat.cd_unidade_local_servico
            )                              AS codigo_vinculo_origem,
            CAST(fat.cd_tipo_funcao AS VARCHAR(50)) AS cargo_codigo,
            NULL                           AS cargo_nome,
            fat.cd_unidade_local_servico   AS unidade_codigo,
            ue.nm_unidade_educacao         AS unidade_nome,
            ue.cd_unidade_administrativa_referencia AS dre_codigo,
            NULL                           AS situacao
        FROM v_servidor_cotic sev
        INNER JOIN v_cargo_base_cotic cba
            ON sev.cd_servidor = cba.cd_servidor
        INNER JOIN funcao_atividade_cargo_servidor fat
            ON fat.cd_cargo_base_servidor = cba.cd_cargo_base_servidor
            AND (fat.dt_cancelamento IS NULL
                 OR fat.dt_fim_funcao_atividade > getdate())
            AND (fat.dt_fim_funcao_atividade IS NULL
                 OR fat.dt_fim_funcao_atividade > getdate())
        LEFT JOIN v_cadastro_unidade_educacao ue
            ON ue.cd_unidade_educacao = fat.cd_unidade_local_servico
        WHERE cba.dt_fim_nomeacao IS NULL
            AND cba.dt_cancelamento IS NULL
    """,
}

SERVIDORES_SERAP_SE1426 = """
        SELECT rf, nome, cpf, situacao, email
        FROM (
            SELECT
                s.cd_registro_funcional  AS rf,
                s.nm_pessoa              AS nome,
                s.cd_cpf_pessoa          AS cpf,
                s.situacao               AS situacao,
                e.dc_dispositivo         AS email,
                ROW_NUMBER() OVER (
                    PARTITION BY s.cd_registro_funcional
                    ORDER BY CASE WHEN s.situacao = 'Ativo' THEN 0 ELSE 1 END
                ) AS rn
            FROM v_servidor_sme_serap s
            LEFT JOIN v_servidor_cotic sc
                ON sc.cd_registro_funcional = s.cd_registro_funcional
            LEFT JOIN v_servidor_email_cotic e
                ON e.cd_servidor = sc.cd_servidor
                AND e.dt_fim IS NULL
            {filtro_where_apenas_ativos}
        ) dedup
        WHERE rn = 1
    """

COMPLEMENTO_SERVIDOR_SE1426 = """
        SELECT
            s.cd_registro_funcional  AS rf,
            s.nm_pessoa              AS nome,
            s.cd_cpf_pessoa          AS cpf,
            e.dc_dispositivo         AS email
        FROM v_servidor_sme_serap s
        LEFT JOIN v_servidor_cotic sc
            ON sc.cd_registro_funcional
               = s.cd_registro_funcional
        LEFT JOIN v_servidor_email_cotic e
            ON e.cd_servidor = sc.cd_servidor
            AND e.dt_fim IS NULL
        WHERE s.cd_registro_funcional = ?
    """

USUARIOS_CORESSO = """
        SELECT login, cpf, nome, email, situacao, dt_atualizacao
        FROM (
            SELECT
                u.usu_login          AS login,
                doc.psd_numero        AS cpf,
                pes.pes_nome          AS nome,
                u.usu_email           AS email,
                u.usu_situacao        AS situacao,
                u.usu_dataAlteracao   AS dt_atualizacao,
                ROW_NUMBER() OVER (
                    PARTITION BY pes.pes_id
                    ORDER BY doc.psd_numero
                ) AS rn
            FROM SYS_Usuario u
            INNER JOIN PES_Pessoa pes ON pes.pes_id = u.pes_id
            LEFT JOIN PES_PessoaDocumento doc
                ON doc.pes_id = pes.pes_id
                AND doc.tdo_id = '{_TDO_ID_CPF}'
            WHERE 1 = 1 {filtro_where}
        ) sub
        WHERE rn = 1
    """

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

DADOS_USUARIO_CORESSO = """
        SELECT
            u.usu_login     AS login,
            doc.psd_numero  AS cpf,
            pes.pes_nome    AS nome,
            u.usu_email     AS email,
            u.usu_situacao  AS situacao,
            g.gru_id        AS gru_id,
            g.gru_nome      AS gru_nome,
            g.sis_id        AS sis_id,
            s.sis_nome      AS sis_nome
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
        LEFT JOIN SYS_UsuarioGrupo ug
            ON ug.usu_id = u.usu_id
        LEFT JOIN SYS_Grupo g
            ON g.gru_id = ug.gru_id
            AND g.gru_situacao = 1
        LEFT JOIN SYS_Sistema s
            ON s.sis_id = g.sis_id
        WHERE {filtro}
        ORDER BY s.sis_nome, g.gru_nome
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
