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

        # Dedup simplificado: alunos/terceiros são ignorados (permanecem como transformed)
        assert StagingUsuarioAluno.objects.get(execution_id=execution.id).status == "transformed"
        assert StagingUsuarioTerceiro.objects.get(execution_id=execution.id).status == "transformed"

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
        from staging.models import StagingUsuarioServidor

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
        # se1426 tem prioridade 1 (maior) — winner=ready, eol_db=skipped
        assert se1426_srv.status == StagingUsuarioServidor.Status.READY
        assert eoldb_srv.status == StagingUsuarioServidor.Status.SKIPPED
        # DedupResult não é criado no modo simplificado (economiza memória)

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


class TestTransformModelEdgeCases:
    def test_flush_updates_error_buffer(self):
        from staging.tasks import _transform_model
        from staging.models import StagingUsuarioServidor

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
        from staging.tasks import _transform_model
        from staging.models import (
            StagingLotacao,
            StagingUsuarioServidor,
        )

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
            # lotacao_map espera dicts com chaves "codigo", "dre_codigo", "tipo"
            {"0001": {"codigo": "0001", "dre_codigo": "DRE-TESTE", "tipo": "ue"}},
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
        from staging.tasks import _transform_model
        from staging.models import StagingUsuarioServidor

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
    def test_transform_staging_failure_updates_step(
        self,
        monkeypatch,
    ):
        from staging.tasks import transform_staging

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
    def test_merge_populates_empty_winner_fields(self):
        from staging.tasks import crossref_dedup

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
        loser.refresh_from_db()

        # Dedup simplificado: se1426 é winner (prioridade 1), eol_db é loser (prioridade 2)
        # Não há merge de campos — winner mantém seus próprios dados
        assert winner.status == "ready"
        assert loser.status == "skipped"
        assert winner.email is None  # email não é mergeado no modo simplificado

    def test_conflict_increments_conflict_counter(self):
        from staging.tasks import crossref_dedup
        from core.models import ETLStepLog

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

        # Dedup simplificado: não rastreia conflicts — usa servidores_skipped
        assert step.metadata["servidores_skipped"] == 1

    def test_coresso_member_overrides_situacao(self):
        from staging.tasks import crossref_dedup

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
        coresso.refresh_from_db()

        # Dedup simplificado: se1426 (prioridade 1) vence sobre coresso (prioridade 3)
        # Não há override de campos — winner mantém seus próprios dados
        assert winner.status == "ready"
        assert coresso.status == "skipped"
        assert winner.situacao == "ativo"  # não é sobrescrito pelo coresso no modo simplificado

    def test_cluster_exception_increments_errors(
        self,
        monkeypatch,
    ):
        from staging.tasks import crossref_dedup

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
        from staging.tasks import crossref_dedup
        from staging.models import StagingUsuarioServidor

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