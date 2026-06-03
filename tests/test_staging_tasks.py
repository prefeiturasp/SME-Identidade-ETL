import pytest

pytestmark = pytest.mark.django_db




def _make_execution(source="se1426"):
    from core.models import ETLExecution
    return ETLExecution.objects.create(source=source)


def _make_servidor(execution_id, rf=None, cpf=None, nome="JOAO SILVA",
                   status=None, source=None):
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
    def test_transforms_raw_servidor(self):
        from staging.tasks import transform_staging
        from staging.models import StagingUsuarioServidor
        from core.models import ETLStepLog

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
        from staging.tasks import transform_staging
        from staging.models import StagingUsuarioServidor

        execution = _make_execution()
        _make_servidor(execution.id, nome="JOSE  ANTONIO  DA   SILVA")
        transform_staging(str(execution.id))

        updated = StagingUsuarioServidor.objects.get(execution_id=execution.id)
        assert "  " not in updated.nome

    def test_invalid_cpf_marks_error_detail(self):
        from staging.tasks import transform_staging
        from staging.models import StagingUsuarioServidor

        execution = _make_execution()
        _make_servidor(execution.id, cpf="11111111111")
        transform_staging(str(execution.id))

        updated = StagingUsuarioServidor.objects.get(execution_id=execution.id)
        assert updated.status == StagingUsuarioServidor.Status.TRANSFORMED
        assert "CPF inválido" in (updated.error_detail or "")

    def test_transforms_raw_aluno(self):
        from staging.tasks import transform_staging
        from staging.models import StagingUsuarioAluno

        execution = _make_execution()
        _make_aluno(execution.id)
        transform_staging(str(execution.id))

        updated = StagingUsuarioAluno.objects.get(execution_id=execution.id)
        assert updated.status == StagingUsuarioAluno.Status.TRANSFORMED

    def test_transforms_raw_terceiro(self):
        from staging.tasks import transform_staging
        from staging.models import StagingUsuarioTerceiro

        execution = _make_execution()
        _make_terceiro(execution.id)
        transform_staging(str(execution.id))

        updated = StagingUsuarioTerceiro.objects.get(execution_id=execution.id)
        assert updated.status == StagingUsuarioTerceiro.Status.TRANSFORMED

    def test_no_raw_records_is_success(self):
        from staging.tasks import transform_staging
        from core.models import ETLStepLog

        execution = _make_execution()
        transform_staging(str(execution.id))

        step = ETLStepLog.objects.get(execution=execution)
        assert step.status == "success"
        assert step.records_out == 0

    def test_normalizes_rf(self):
        from staging.tasks import transform_staging
        from staging.models import StagingUsuarioServidor

        execution = _make_execution()
        _make_servidor(execution.id, rf="0012345")
        transform_staging(str(execution.id))

        updated = StagingUsuarioServidor.objects.get(execution_id=execution.id)

        assert updated.rf == "12345"




class TestCrossrefDedup:
    def test_aluno_and_terceiro_become_ready(self):
        from staging.tasks import crossref_dedup
        from staging.models import StagingUsuarioAluno, StagingUsuarioTerceiro

        execution = _make_execution()
        _make_aluno(execution.id, status="transformed")
        _make_terceiro(execution.id, status="transformed")
        crossref_dedup(str(execution.id))

        assert StagingUsuarioAluno.objects.get(execution_id=execution.id).status == "ready"
        assert StagingUsuarioTerceiro.objects.get(execution_id=execution.id).status == "ready"

    def test_single_servidor_becomes_ready(self):
        from staging.tasks import crossref_dedup
        from staging.models import StagingUsuarioServidor

        execution = _make_execution()
        srv = _make_servidor(execution.id, status="transformed")
        crossref_dedup(str(execution.id))

        updated = StagingUsuarioServidor.objects.get(id=srv.id)
        assert updated.status == StagingUsuarioServidor.Status.READY

    def test_dedup_merges_same_cpf(self):
        from staging.tasks import crossref_dedup
        from staging.models import StagingUsuarioServidor, DedupResult

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
        from staging.tasks import crossref_dedup
        from core.models import ETLStepLog

        execution = _make_execution()

        _make_aluno(execution.id, status="transformed")
        crossref_dedup(str(execution.id))

        step = ETLStepLog.objects.get(
            execution=execution,
            step_name="crossref_dedup",
        )
        assert step.status == "success"

    def test_servidor_without_valid_cpf_or_rf_gets_error(self):
        from staging.tasks import crossref_dedup
        from staging.models import StagingUsuarioServidor

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
    def _srv(self, cpf=None, rf=None):
        from types import SimpleNamespace
        return SimpleNamespace(cpf=cpf, rf=rf)

    def test_cpf_exact_match(self):
        from staging.tasks import _determine_match_type
        from staging.models import DedupResult
        w = self._srv(cpf="52998224725", rf="12345")
        l = self._srv(cpf="52998224725", rf="12345")
        assert _determine_match_type(w, l) == DedupResult.MatchType.CPF_EXACT

    def test_rf_exact_match(self):
        from staging.tasks import _determine_match_type
        from staging.models import DedupResult
        w = self._srv(cpf=None, rf="12345")
        l = self._srv(cpf=None, rf="12345")
        assert _determine_match_type(w, l) == DedupResult.MatchType.RF_EXACT

    def test_cpf_rf_cross_match(self):
        from staging.tasks import _determine_match_type
        from staging.models import DedupResult
        w = self._srv(cpf="52998224725", rf="12345")
        l = self._srv(cpf="39053344705", rf="99999")
        assert _determine_match_type(w, l) == DedupResult.MatchType.CPF_RF_CROSS




class TestCheckConflicts:
    def _srv(self, nome=None, cargo=None, situacao=None):
        from types import SimpleNamespace
        return SimpleNamespace(nome=nome, cargo=cargo, situacao=situacao)

    def test_no_conflict_when_fields_match(self):
        from staging.tasks import _check_conflicts
        w = self._srv(nome="JOAO SILVA", cargo="PROF", situacao="ativo")
        l = self._srv(nome="JOAO SILVA", cargo="PROF", situacao="ativo")
        assert _check_conflicts(w, l) is False

    def test_conflict_when_nome_differs(self):
        from staging.tasks import _check_conflicts
        w = self._srv(nome="JOAO SILVA")
        l = self._srv(nome="JOAO SANTOS")
        assert _check_conflicts(w, l) is True

    def test_no_conflict_when_one_side_empty(self):
        from staging.tasks import _check_conflicts
        w = self._srv(nome="JOAO SILVA", cargo=None)
        l = self._srv(nome="JOAO SILVA", cargo="PROF")
        assert _check_conflicts(w, l) is False
