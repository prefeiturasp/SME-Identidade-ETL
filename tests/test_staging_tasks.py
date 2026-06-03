import pytest

from core.models import ETLStepLog
from staging.models import (
    DedupResult,
    StagingLotacao,
    StagingUsuarioAluno,
    StagingUsuarioServidor,
    StagingUsuarioTerceiro,
)
from staging.tasks import (
    _determine_match_type,
    _transform_model,
    crossref_dedup,
    transform_staging,
)

pytestmark = pytest.mark.django_db

def _make_execution(source="se1426"):
    """Cria uma execução ETL para uso nos testes.

    Args:
        source (str): Fonte associada à execução.

    Returns:
        ETLExecution: Instância criada.
    """
    from core.models import ETLExecution
    return ETLExecution.objects.create(source=source)

def _make_servidor(execution_id, rf=None, cpf=None, nome="JOAO SILVA",
                   status=None, source=None):
    """Cria um registro de servidor para os cenários de teste.

    Args:
        execution_id (UUID): Identificador da execução ETL.
        rf (str | None): Registro funcional.
        cpf (str | None): CPF do servidor.
        nome (str): Nome do servidor.
        status (str | None): Status do registro.
        source (str | None): Fonte dos dados.

    Returns:
        StagingUsuarioServidor: Registro criado.
    """
    from staging.models import StagingUsuarioServidor
    if status is None:
        status = StagingUsuarioServidor.Status.RAW
    if source is None:
        source = StagingUsuarioServidor.Source.SE1426
    return StagingUsuarioServidor.objects.create(
        execution_id=execution_id,
        rf=rf or "12345",
        cpf=cpf or "52998224725",
        nome=nome,
        status=status,
        source=source,
    )

def _make_aluno(execution_id, status=None):
    """Cria um registro de aluno para os testes.

    Args:
        execution_id (UUID): Identificador da execução ETL.
        status (str | None): Status do registro.

    Returns:
        StagingUsuarioAluno: Registro criado.
    """
    from staging.models import StagingUsuarioAluno
    if status is None:
        status = StagingUsuarioAluno.Status.RAW
    return StagingUsuarioAluno.objects.create(
        execution_id=execution_id,
        matricula="99999",
        nome="MARIA SOUZA",
        status=status,
    )

def _make_terceiro(execution_id, status=None):
    """Cria um registro de terceiro para os testes.

    Args:
        execution_id (UUID): Identificador da execução ETL.
        status (str | None): Status do registro.

    Returns:
        StagingUsuarioTerceiro: Registro criado.
    """
    from staging.models import StagingUsuarioTerceiro
    if status is None:
        status = StagingUsuarioTerceiro.Status.RAW
    return StagingUsuarioTerceiro.objects.create(
        execution_id=execution_id,
        cpf="52998224725",
        nome="CARLOS LIMA",
        status=status,
    )


class TestTransformStaging:
    """Testes relacionados à transformação dos dados de staging."""

    def test_transforms_raw_servidor(self):
        """Deve transformar um servidor com status RAW."""
        execution = _make_execution()
        _make_servidor(execution.id)
        transform_staging(str(execution.id))

        updated = StagingUsuarioServidor.objects.get(execution_id=execution.id)
        assert updated.status == StagingUsuarioServidor.Status.TRANSFORMED
        assert updated.nome == "Joao Silva"
        assert updated.cpf == "52998224725"

        step = ETLStepLog.objects.get(execution=execution)
        assert step.status == "success"
        assert step.records_out == 1

    def test_normalizes_nome(self):
        """Deve normalizar espaços excedentes no nome."""
        execution = _make_execution()
        _make_servidor(execution.id, nome="JOSE  ANTONIO  DA   SILVA")
        transform_staging(str(execution.id))

        updated = StagingUsuarioServidor.objects.get(execution_id=execution.id)
        assert "  " not in updated.nome

    def test_invalid_cpf_marks_error_detail(self):
        """Deve registrar erro quando o CPF for inválido."""
        execution = _make_execution()
        _make_servidor(execution.id, cpf="11111111111")
        transform_staging(str(execution.id))

        updated = StagingUsuarioServidor.objects.get(execution_id=execution.id)
        assert updated.status == StagingUsuarioServidor.Status.TRANSFORMED
        assert "CPF inválido" in (updated.error_detail or "")

    def test_transforms_raw_aluno(self):
        """Deve transformar registros de alunos."""
        execution = _make_execution()
        _make_aluno(execution.id)
        transform_staging(str(execution.id))

        updated = StagingUsuarioAluno.objects.get(execution_id=execution.id)
        assert updated.status == StagingUsuarioAluno.Status.TRANSFORMED

    def test_transforms_raw_terceiro(self):
        """Deve transformar registros de terceiros."""
        execution = _make_execution()
        _make_terceiro(execution.id)
        transform_staging(str(execution.id))

        updated = StagingUsuarioTerceiro.objects.get(execution_id=execution.id)
        assert updated.status == StagingUsuarioTerceiro.Status.TRANSFORMED

    def test_no_raw_records_is_success(self):
        """Deve finalizar com sucesso quando não houver registros RAW."""
        execution = _make_execution()
        transform_staging(str(execution.id))

        step = ETLStepLog.objects.get(execution=execution)
        assert step.status == "success"
        assert step.records_out == 0

    def test_normalizes_rf(self):
        """Deve normalizar RF removendo zeros à esquerda."""
        execution = _make_execution()
        _make_servidor(execution.id, rf="0012345")
        transform_staging(str(execution.id))

        updated = StagingUsuarioServidor.objects.get(execution_id=execution.id)

        assert updated.rf == "12345"


class TestCrossrefDedup:
    """Testes relacionados ao processo de deduplicação."""

    def test_aluno_and_terceiro_become_ready(self):
        """Deve marcar alunos e terceiros transformados como prontos."""
        execution = _make_execution()
        _make_aluno(execution.id, status="transformed")
        _make_terceiro(execution.id, status="transformed")
        crossref_dedup(str(execution.id))

        assert StagingUsuarioAluno.objects.get(
            execution_id=execution.id).status == "ready"
        assert StagingUsuarioTerceiro.objects.get(
            execution_id=execution.id).status == "ready"

    def test_single_servidor_becomes_ready(self):
        """Deve marcar um servidor transformado como pronto."""
        execution = _make_execution()
        srv = _make_servidor(execution.id, status="transformed")
        crossref_dedup(str(execution.id))

        updated = StagingUsuarioServidor.objects.get(id=srv.id)
        assert updated.status == StagingUsuarioServidor.Status.READY

    def test_dedup_merges_same_cpf(self):
        """Deve realizar deduplicação de registros com o mesmo CPF."""
        execution = _make_execution()

        se1426_srv = _make_servidor(
            execution.id, rf="12345", cpf="52998224725",
            status="transformed", source="se1426"
        )
        eoldb_srv = _make_servidor(
            execution.id, rf="12345", cpf="52998224725",
            status="transformed", source="eol_db"
        )

        crossref_dedup(str(execution.id))


        se1426_srv.refresh_from_db()
        eoldb_srv.refresh_from_db()
        assert se1426_srv.status == StagingUsuarioServidor.Status.READY
        assert eoldb_srv.status == StagingUsuarioServidor.Status.SKIPPED
        assert DedupResult.objects.filter(execution_id=execution.id).exists()

    def test_no_servidores_succeeds(self):
        """Deve concluir a deduplicação com sucesso sem servidores."""
        execution = _make_execution()

        _make_aluno(execution.id, status="transformed")
        crossref_dedup(str(execution.id))

        step = ETLStepLog.objects.get(
            execution=execution,
            step_name="crossref_dedup",
        )
        assert step.status == "success"

    def test_servidor_without_valid_cpf_or_rf_gets_error(self):
        """Deve tratar registros sem CPF e RF válidos sem lançar exceção."""
        execution = _make_execution()

        StagingUsuarioServidor.objects.create(
            execution_id=execution.id,
            cpf=None,
            rf=None,
            nome="SEM IDENTIFICACAO",
            status="transformed",
            source="se1426",
        )
        # Nao deve levantar excecao
        crossref_dedup(str(execution.id))


class TestDetermineMatchType:
    """Testes para determinação do tipo de correspondência."""

    def _srv(self, cpf=None, rf=None):
        """Cria um objeto simplificado para testes de correspondência."""
        from types import SimpleNamespace
        return SimpleNamespace(cpf=cpf, rf=rf)

    def test_cpf_exact_match(self):
        """Cria um objeto simplificado para testes de correspondência."""
        first = self._srv(cpf="52998224725", rf="12345")
        second = self._srv(cpf="52998224725", rf="12345")
        assert _determine_match_type(first, second) == DedupResult.MatchType.CPF_EXACT

    def test_rf_exact_match(self):
        """Deve identificar correspondência exata por RF."""
        first = self._srv(cpf=None, rf="12345")
        second = self._srv(cpf=None, rf="12345")
        assert _determine_match_type(first, second) == DedupResult.MatchType.RF_EXACT

    def test_cpf_rf_cross_match(self):
        """Deve identificar correspondência cruzada entre CPF e RF."""
        """Não deve identificar conflito quando os campos coincidem."""
        first = self._srv(cpf="52998224725", rf="12345")
        second = self._srv(cpf="39053344705", rf="99999")
        assert _determine_match_type(first,
                                     second) == DedupResult.MatchType.CPF_RF_CROSS


class TestCheckConflicts:
    """Testes para detecção de conflitos entre registros."""

    def _srv(self, nome=None, cargo=None, situacao=None):
        """Cria um objeto simplificado para testes de conflito."""
        from types import SimpleNamespace
        return SimpleNamespace(nome=nome, cargo=cargo, situacao=situacao)

    def test_no_conflict_when_fields_match(self):
        """Não deve identificar conflito quando os campos coincidem."""
        from staging.tasks import _check_conflicts
        first = self._srv(nome="JOAO SILVA", cargo="PROF", situacao="ativo")
        second = self._srv(nome="JOAO SILVA", cargo="PROF", situacao="ativo")
        assert _check_conflicts(first, second) is False

    def test_conflict_when_nome_differs(self):
        """Deve identificar conflito quando os nomes diferem."""
        from staging.tasks import _check_conflicts
        first = self._srv(nome="JOAO SILVA")
        second = self._srv(nome="JOAO SANTOS")
        assert _check_conflicts(first, second) is True

    def test_no_conflict_when_one_side_empty(self):
        """Não deve identificar conflito quando apenas um lado possui valor."""
        from staging.tasks import _check_conflicts
        first = self._srv(nome="JOAO SILVA", cargo=None)
        second = self._srv(nome="JOAO SILVA", cargo="PROF")
        assert _check_conflicts(first, second) is False


class TestTransformModelEdgeCases:
    """Testes de cenários de borda da transformação de modelos."""

    def test_flush_updates_error_buffer(self):
        """Deve persistir alterações pendentes ao atualizar o buffer de erros."""
        execution = _make_execution()

        StagingUsuarioServidor.objects.create(
            execution_id=execution.id,
            cpf="52998224725",
            nome="JOAO",
            status=StagingUsuarioServidor.Status.RAW,
            source="se1426",
        )

        transformed, errors = _transform_model(
            StagingUsuarioServidor,
            execution.id,
            {},
            bulk_size=1,
            extra_fields={"rf_field": True},
        )

        assert transformed == 1
        assert errors == 0

    def test_populates_dre_and_ue_from_lotacao(self):
        """Deve preencher DRE e UE a partir da lotação associada."""
        execution = _make_execution()

        lotacao = StagingLotacao.objects.create(
            codigo="0001",
            nome="EMEF TESTE",
            tipo="ue",
            dre_codigo="DRE-TESTE",
        )

        srv = StagingUsuarioServidor.objects.create(
            execution_id=execution.id,
            rf="12345",
            cpf="52998224725",
            nome="JOAO",
            lotacao="0001",
            status=StagingUsuarioServidor.Status.RAW,
            source="se1426",
        )

        _transform_model(
            StagingUsuarioServidor,
            execution.id,
            {"0001": lotacao},
            bulk_size=100,
            extra_fields={
                "rf_field": True,
                "lotacao_field": True,
            },
        )

        srv.refresh_from_db()

        assert srv.dre == "DRE-TESTE"
        assert srv.ue == "0001"

    def test_transform_model_marks_error_on_record_exception(self,
        monkeypatch,
    ):
        """Deve marcar o registro como erro quando a transformação falhar."""
        execution = _make_execution()

        srv = StagingUsuarioServidor.objects.create(
            execution_id=execution.id,
            rf="12345",
            cpf="52998224725",
            nome="JOAO",
            status=StagingUsuarioServidor.Status.RAW,
            source="se1426",
        )

        class ControlledTransformError(RuntimeError):
            """Erro controlado para teste."""

        def explode(*args, **kwargs):
            raise ControlledTransformError("forced transform error")

        monkeypatch.setattr(
            "staging.tasks.normalize_cpf",
            explode,
        )

        transformed, errors = _transform_model(
            StagingUsuarioServidor,
            execution.id,
            {},
            bulk_size=1,
            extra_fields={"rf_field": True},
        )

        srv.refresh_from_db()

        assert transformed == 0
        assert errors == 1
        assert srv.status == StagingUsuarioServidor.Status.ERROR
        assert "Transform error" in srv.error_detail


class TestTransformStagingFailures:
    """Testes de tratamento de falhas na etapa de transformação."""

    def test_transform_staging_failure_updates_step(
        self,
        monkeypatch,
    ):
        """Deve propagar falhas ocorridas durante a transformação."""
        execution = _make_execution()

        class TransformExplodedError(RuntimeError):
            """Erro controlado para teste."""

        def explode(*args, **kwargs):
            raise TransformExplodedError("transform exploded")

        monkeypatch.setattr(
            "staging.tasks._transform_model",
            explode,
        )

        with pytest.raises(
            TransformExplodedError,
            match="transform exploded",
        ):
            transform_staging(str(execution.id))


class TestCrossrefDedupExtraBranches:
    """Testes para cobrir fluxos alternativos da deduplicação."""

    def test_merge_populates_empty_winner_fields(self):
        """Deve tratar erros ocorridos durante o agrupamento de registros."""
        execution = _make_execution()

        winner = _make_servidor(
            execution.id,
            cpf="52998224725",
            rf="12345",
            status="transformed",
            source="se1426",
        )

        winner.email = None
        winner.save(update_fields=["email"])

        loser = _make_servidor(
            execution.id,
            cpf="52998224725",
            rf="12345",
            status="transformed",
            source="eol_db",
        )

        loser.email = "merged@sme.sp.gov.br"
        loser.save(update_fields=["email"])

        crossref_dedup(str(execution.id))

        winner.refresh_from_db()

        assert winner.email == "merged@sme.sp.gov.br"

    def test_conflict_increments_conflict_counter(self):
        """Deve incrementar o contador de conflitos ao detectar divergências."""
        execution = _make_execution()

        _make_servidor(
            execution.id,
            cpf="52998224725",
            rf="12345",
            nome="JOAO",
            status="transformed",
            source="se1426",
        )

        _make_servidor(
            execution.id,
            cpf="52998224725",
            rf="12345",
            nome="MARIA",
            status="transformed",
            source="eol_db",
        )

        crossref_dedup(str(execution.id))

        step = ETLStepLog.objects.get(
            execution=execution,
            step_name="crossref_dedup",
        )

        assert step.metadata["servidores_conflicts"] == 1

    def test_coresso_member_overrides_situacao(self):
        """Deve priorizar a situação proveniente da fonte Coresso."""
        execution = _make_execution()

        winner = _make_servidor(
            execution.id,
            cpf="52998224725",
            rf="12345",
            status="transformed",
            source="se1426",
        )

        winner.situacao = "ativo"
        winner.save(update_fields=["situacao"])

        coresso = _make_servidor(
            execution.id,
            cpf="52998224725",
            rf="12345",
            status="transformed",
            source="coresso",
        )

        coresso.situacao = "afastado"
        coresso.save(update_fields=["situacao"])

        crossref_dedup(str(execution.id))

        winner.refresh_from_db()

        assert winner.situacao == "afastado"

    def test_cluster_exception_increments_errors(
        self,
        monkeypatch,
    ):
        """Deve contabilizar erros quando ocorrer falha no agrupamento."""
        execution = _make_execution()

        _make_servidor(
            execution.id,
            cpf="52998224725",
            rf="12345",
            status="transformed",
        )

        class ClusterExplodedError(RuntimeError):
            """Erro controlado para teste."""

        def explode(*args, **kwargs):
            raise ClusterExplodedError("cluster exploded")

        monkeypatch.setattr(
            "staging.tasks.build_dedup_key",
            explode,
        )

        crossref_dedup(str(execution.id))

    def test_crossref_dedup_failure_updates_step(
        self,
        monkeypatch,
    ):
        """Deve propagar falhas críticas durante a deduplicação."""
        execution = _make_execution()

        class FatalDedupError(RuntimeError):
            """Erro controlado para teste."""

        def explode(*args, **kwargs):
            raise FatalDedupError("fatal dedup error")

        monkeypatch.setattr(
            StagingUsuarioServidor.objects,
            "filter",
            explode,
        )

        with pytest.raises(
            FatalDedupError,
            match="fatal dedup error",
        ):
            crossref_dedup(str(execution.id))
