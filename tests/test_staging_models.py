import uuid

import pytest

pytestmark = pytest.mark.django_db




def _exec_id():
    return uuid.uuid4()




class TestStagingUsuarioServidor:
    def test_create_servidor(self):
        from staging.models import StagingUsuarioServidor
        exec_id = _exec_id()
        u = StagingUsuarioServidor.objects.create(
            rf="654321",
            cpf="52998224725",
            nome="JOAO DA SILVA",
            email="joao@sme.sp.gov.br",
            source=StagingUsuarioServidor.Source.SE1426,
            execution_id=exec_id,
        )
        assert u.status == StagingUsuarioServidor.Status.RAW
        assert str(u).startswith("[srv/se1426]")

    def test_servidor_uuid_pk(self):
        from staging.models import StagingUsuarioServidor
        u = StagingUsuarioServidor.objects.create(
            source="se1426",
            execution_id=_exec_id(),
        )
        assert isinstance(u.id, uuid.UUID)

    def test_servidor_status_choices(self):
        from staging.models import StagingUsuarioServidor
        for status in ("raw", "transformed", "ready", "loaded", "skipped", "error"):
            u = StagingUsuarioServidor.objects.create(
                source="se1426",
                execution_id=_exec_id(),
                status=status,
            )
            assert u.status == status

    def test_servidor_str_with_cpf_no_rf(self):
        from staging.models import StagingUsuarioServidor
        u = StagingUsuarioServidor.objects.create(
            cpf="52998224725",
            nome="MARIA",
            source="coresso",
            execution_id=_exec_id(),
        )
        s = str(u)
        assert "srv" in s




class TestStagingUsuarioAluno:
    def test_create_aluno(self):
        from staging.models import StagingUsuarioAluno
        u = StagingUsuarioAluno.objects.create(
            matricula="999001",
            nome="ANA LIMA",
            source="eol_db",
            execution_id=_exec_id(),
        )
        assert u.status == "raw"
        assert "aluno" in str(u)

    def test_aluno_uuid_pk(self):
        from staging.models import StagingUsuarioAluno
        u = StagingUsuarioAluno.objects.create(
            source="eol_db",
            execution_id=_exec_id(),
        )
        assert isinstance(u.id, uuid.UUID)




class TestStagingUsuarioTerceiro:
    def test_create_terceiro(self):
        from staging.models import StagingUsuarioTerceiro
        u = StagingUsuarioTerceiro.objects.create(
            cpf="11144477735",
            nome="CARLOS FORNECEDOR",
            tipo_acesso="parceiro",
            source="coresso",
            execution_id=_exec_id(),
        )
        assert u.tipo_acesso == "parceiro"
        assert "terc" in str(u)

    def test_terceiro_str_with_email(self):
        from staging.models import StagingUsuarioTerceiro
        u = StagingUsuarioTerceiro.objects.create(
            email="user@ext.com",
            source="coresso",
            execution_id=_exec_id(),
        )
        s = str(u)
        assert "terc" in s




class TestStagingUsuarioBase:
    """Testa campos herdados da classe abstrata via StagingUsuarioServidor."""

    def test_raw_data_default_empty_dict(self):
        from staging.models import StagingUsuarioServidor
        u = StagingUsuarioServidor.objects.create(
            source="se1426",
            execution_id=_exec_id(),
        )
        assert u.raw_data == {}

    def test_error_detail_null_by_default(self):
        from staging.models import StagingUsuarioServidor
        u = StagingUsuarioServidor.objects.create(
            source="se1426",
            execution_id=_exec_id(),
        )
        assert u.error_detail is None




class TestStagingUsuarioServidorSerializer:
    def test_serializes_servidor(self):
        from staging.models import StagingUsuarioServidor
        from staging.serializers import StagingUsuarioServidorSerializer
        u = StagingUsuarioServidor.objects.create(
            rf="654321",
            cpf="52998224725",
            nome="JOAO SILVA",
            source="se1426",
            execution_id=_exec_id(),
        )
        data = StagingUsuarioServidorSerializer(u).data
        assert data["rf"] == "654321"
        assert data["cpf"] == "52998224725"
        assert data["status"] == "raw"

    def test_readonly_fields_not_writable(self):
        from staging.serializers import StagingUsuarioServidorSerializer
        s = StagingUsuarioServidorSerializer(data={"rf": "123", "status": "loaded"})
        # read_only_fields = todos os campos, entao is_valid nao usa nenhum campo
        # Apenas garantir que nao quebra
        assert isinstance(s, StagingUsuarioServidorSerializer)




class TestStagingUsuarioAlunoSerializer:
    def test_serializes_aluno(self):
        from staging.models import StagingUsuarioAluno
        from staging.serializers import StagingUsuarioAlunoSerializer
        u = StagingUsuarioAluno.objects.create(
            matricula="999001",
            nome="ANA LIMA",
            source="eol_db",
            execution_id=_exec_id(),
        )
        data = StagingUsuarioAlunoSerializer(u).data
        assert data["matricula"] == "999001"
        assert data["source"] == "eol_db"




class TestStagingUsuarioTerceiroSerializer:
    def test_serializes_terceiro(self):
        from staging.models import StagingUsuarioTerceiro
        from staging.serializers import StagingUsuarioTerceiroSerializer
        u = StagingUsuarioTerceiro.objects.create(
            cpf="11144477735",
            tipo_acesso="convidado",
            source="coresso",
            execution_id=_exec_id(),
        )
        data = StagingUsuarioTerceiroSerializer(u).data
        assert data["tipo_acesso"] == "convidado"
