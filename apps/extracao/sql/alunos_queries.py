"""Consultas SQL do domínio de alunos."""

FILTRO_MATRICULAS_ATIVAS_EOL = "WHERE mat.st_matricula IN (1, 5, 6, 10, 13)"

ALUNOS_EOL = """
        SELECT
            a.cd_aluno                                  AS matricula,
            a.nm_aluno                                  AS nome,
            a.cd_cpf_aluno                               AS cpf,
            mat.st_matricula                             AS situacao,
            CAST(mt.cd_turma_escola AS VARCHAR(20))      AS cod_escola,
            CAST(mat.cd_serie_ensino AS VARCHAR(20))     AS turma,
            CAST(ue.cd_unidade_educacao AS VARCHAR(20))  AS cod_ue,
            CAST(ue.cd_unidade_administrativa_referencia
                AS VARCHAR(20))                          AS cod_dre
        FROM aluno a
        INNER JOIN v_matricula_cotic mat ON mat.cd_aluno = a.cd_aluno
        INNER JOIN matricula_turma_escola mt
            ON mt.cd_matricula = mat.cd_matricula
        INNER JOIN v_cadastro_unidade_educacao ue
            ON ue.cd_unidade_educacao = mat.cd_escola
        {filtro_where}
    """
