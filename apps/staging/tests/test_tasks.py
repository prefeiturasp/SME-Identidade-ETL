"""Testes para apps.staging.tasks."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.staging.models import (
    UsuarioAlunoStaging,
    UsuarioServidorStaging,
    UsuarioTerceiroStaging,
)
from apps.staging.tasks import (
    deduplicar_identidades,
    persistir_extracao_staging,
    transformar_staging,
)


@pytest.mark.django_db
class TestTransformarStaging:
    def test_marca_pronto_quando_tem_nome_e_cpf(self) -> None:
        id_execucao = uuid.uuid4()
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="se1426",
            nome="Ana Lima",
            cpf="12345678901",
            situacao="extraido",
        )

        resultado = transformar_staging(str(id_execucao))

        assert resultado["servidor"] == 1
        assert resultado["erros"] == 0
        usuario = UsuarioServidorStaging.objects.get()
        assert usuario.situacao == "pronto"

    def test_marca_erro_quando_sem_nome_e_sem_cpf(self) -> None:
        id_execucao = uuid.uuid4()
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="se1426",
            nome="",
            cpf="",
            situacao="extraido",
        )

        resultado = transformar_staging(str(id_execucao))

        assert resultado["servidor"] == 0
        assert resultado["erros"] == 1
        usuario = UsuarioServidorStaging.objects.get()
        assert usuario.situacao == "erro"
        assert usuario.detalhe_erro == "nome e CPF ausentes"

    def test_processa_os_tres_tipos_de_usuario(self) -> None:
        id_execucao = uuid.uuid4()
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="se1426",
            nome="Servidor",
            cpf="11111111111",
            situacao="extraido",
        )
        UsuarioAlunoStaging.objects.create(
            id_execucao=id_execucao,
            fonte="eol_db",
            nome="Aluno",
            cpf="22222222222",
            situacao="extraido",
        )
        UsuarioTerceiroStaging.objects.create(
            id_execucao=id_execucao,
            fonte="coresso",
            nome="Terceiro",
            cpf="33333333333",
            situacao="extraido",
        )

        resultado = transformar_staging(str(id_execucao))

        assert resultado == {
            "servidor": 1,
            "aluno": 1,
            "terceiro": 1,
            "erros": 0,
            "total": 3,
        }

    def test_ignora_registros_que_nao_estao_extraidos(self) -> None:
        id_execucao = uuid.uuid4()
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="se1426",
            nome="Ana Lima",
            cpf="12345678901",
            situacao="pronto",
        )

        resultado = transformar_staging(str(id_execucao))

        assert resultado["servidor"] == 0

    def test_nao_afeta_registros_de_outra_execucao(self) -> None:
        id_execucao = uuid.uuid4()
        outra_execucao = uuid.uuid4()
        UsuarioServidorStaging.objects.create(
            id_execucao=outra_execucao,
            fonte="se1426",
            nome="Outro",
            cpf="99999999999",
            situacao="extraido",
        )

        resultado = transformar_staging(str(id_execucao))

        assert resultado["servidor"] == 0
        outro = UsuarioServidorStaging.objects.get(id_execucao=outra_execucao)
        assert outro.situacao == "extraido"


@pytest.mark.django_db
class TestDeduplicarIdentidades:
    def test_mantem_registro_de_fonte_prioritaria_por_cpf(self) -> None:
        id_execucao = uuid.uuid4()
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="se1426",
            cpf="12345678901",
            rf="",
            situacao="pronto",
        )
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="coresso",
            cpf="12345678901",
            rf="",
            situacao="pronto",
        )

        resultado = deduplicar_identidades({}, str(id_execucao))

        assert resultado["ignorados"] == 1
        situacoes = set(
            UsuarioServidorStaging.objects.values_list("fonte", "situacao")
        )
        assert ("se1426", "pronto") in situacoes
        assert ("coresso", "ignorado") in situacoes

    def test_mantem_registro_de_fonte_prioritaria_por_rf(self) -> None:
        id_execucao = uuid.uuid4()
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="se1426",
            cpf="",
            rf="1234567",
            situacao="pronto",
        )
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="coresso",
            cpf="",
            rf="1234567",
            situacao="pronto",
        )

        resultado = deduplicar_identidades({}, str(id_execucao))

        assert resultado["ignorados"] == 1

    def test_sem_duplicatas_nao_ignora_nada(self) -> None:
        id_execucao = uuid.uuid4()
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="se1426",
            cpf="11111111111",
            situacao="pronto",
        )
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="coresso",
            cpf="22222222222",
            situacao="pronto",
        )

        resultado = deduplicar_identidades({}, str(id_execucao))

        assert resultado["ignorados"] == 0

    def test_preserva_chaves_do_resultado_anterior(self) -> None:
        id_execucao = uuid.uuid4()

        resultado = deduplicar_identidades(
            {"servidor": 3, "erros": 0}, str(id_execucao)
        )

        assert resultado["servidor"] == 3
        assert resultado["erros"] == 0
        assert resultado["ignorados"] == 0

    def test_total_deduplicado_exclui_ignorados(self) -> None:
        id_execucao = uuid.uuid4()
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="se1426",
            cpf="12345678901",
            rf="",
            situacao="pronto",
        )
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="coresso",
            cpf="12345678901",
            rf="",
            situacao="pronto",
        )
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="se1426",
            cpf="99988877766",
            rf="",
            situacao="pronto",
        )

        resultado = deduplicar_identidades({}, str(id_execucao))

        assert resultado["ignorados"] == 1
        assert resultado["total_deduplicado"] == 2

    def test_deduplica_terceiro_com_mesmo_cpf_na_mesma_fonte(self) -> None:
        """CoreSSO pode gerar 2 linhas de terceiro p/ a mesma pessoa.

        Reproduz o cenário real observado em QA: PES_PessoaDocumento
        com mais de 1 registro de CPF para a mesma pessoa fazia o
        LEFT JOIN de extração retornar 2 linhas idênticas — ambas
        disputavam a criação simultânea do mesmo usuário no Keycloak
        (409 User exists). A extração já foi corrigida (ROW_NUMBER),
        mas a dedup deve seguir cobrindo isso como rede de segurança.
        """
        id_execucao = uuid.uuid4()
        UsuarioTerceiroStaging.objects.create(
            id_execucao=id_execucao,
            fonte="coresso",
            cpf="30202700810",
            rf="",
            nome="Luciana",
            situacao="pronto",
        )
        UsuarioTerceiroStaging.objects.create(
            id_execucao=id_execucao,
            fonte="coresso",
            cpf="30202700810",
            rf="",
            nome="Luciana",
            situacao="pronto",
        )

        resultado = deduplicar_identidades({}, str(id_execucao))

        assert resultado["ignorados"] == 1
        situacoes = list(
            UsuarioTerceiroStaging.objects.values_list(
                "situacao", flat=True
            )
        )
        assert situacoes.count("pronto") == 1
        assert situacoes.count("ignorado") == 1

    def test_cpf_igual_em_modelos_diferentes_nao_e_deduplicado(self) -> None:
        """Servidor e Terceiro com o mesmo CPF não competem entre si.

        A dedup roda por modelo — são tipos de vínculo distintos
        (ex.: servidor efetivo que também é terceirizado em outro
        contrato), não a mesma linha de staging disputando o mesmo
        registro de Keycloak.
        """
        id_execucao = uuid.uuid4()
        UsuarioServidorStaging.objects.create(
            id_execucao=id_execucao,
            fonte="se1426",
            cpf="12345678901",
            rf="1234567",
            situacao="pronto",
        )
        UsuarioTerceiroStaging.objects.create(
            id_execucao=id_execucao,
            fonte="coresso",
            cpf="12345678901",
            rf="",
            situacao="pronto",
        )

        resultado = deduplicar_identidades({}, str(id_execucao))

        assert resultado["ignorados"] == 0
        assert (
            UsuarioServidorStaging.objects.get().situacao == "pronto"
        )
        assert (
            UsuarioTerceiroStaging.objects.get().situacao == "pronto"
        )


@pytest.mark.django_db
class TestPersistirExtracaoStaging:
    def _registro(self, **kwargs: Any) -> Any:
        from apps.extracao.tasks import RegistroIdentidade

        defaults = {"fonte": "se1426", "tipo": "servidor"}
        defaults.update(kwargs)
        return RegistroIdentidade(**defaults)  # type: ignore[arg-type]

    def test_persiste_servidor(self) -> None:
        id_execucao = uuid.uuid4()
        registros = [
            self._registro(
                tipo="servidor",
                rf="1234567",
                nome="Ana Lima",
                cpf="12345678901",
                cargo="DIRETOR DE ESCOLA",
            )
        ]

        total = persistir_extracao_staging(
            registros, id_execucao=str(id_execucao)
        )

        assert total == 1
        usuario = UsuarioServidorStaging.objects.get()
        assert usuario.rf == "1234567"
        assert usuario.nome == "Ana Lima"
        assert usuario.cargo == "DIRETOR DE ESCOLA"
        assert usuario.situacao == "extraido"
        assert str(usuario.id_execucao) == str(id_execucao)

    def test_persiste_aluno(self) -> None:
        id_execucao = uuid.uuid4()
        registros = [
            self._registro(
                fonte="eol_alunos",
                tipo="aluno",
                nome="João Souza",
                matricula="M001",
                cod_escola="123",
                turma="5A",
            )
        ]

        total = persistir_extracao_staging(
            registros, id_execucao=str(id_execucao)
        )

        assert total == 1
        aluno = UsuarioAlunoStaging.objects.get()
        assert aluno.matricula == "M001"
        assert aluno.cod_escola == "123"
        assert aluno.situacao == "extraido"

    def test_persiste_terceiro(self) -> None:
        id_execucao = uuid.uuid4()
        registros = [
            self._registro(
                fonte="coresso",
                tipo="terceiro",
                nome="Terceirizado X",
                cpf="11122233344",
                tipo_acesso="legado-coresso",
            )
        ]

        total = persistir_extracao_staging(
            registros, id_execucao=str(id_execucao)
        )

        assert total == 1
        terceiro = UsuarioTerceiroStaging.objects.get()
        assert terceiro.tipo_acesso == "legado-coresso"
        assert terceiro.situacao == "extraido"

    def test_persiste_multiplos_tipos_em_lote(self) -> None:
        id_execucao = uuid.uuid4()
        registros = [
            self._registro(tipo="servidor", nome="Servidor 1"),
            self._registro(fonte="eol_alunos", tipo="aluno", nome="Aluno 1"),
            self._registro(
                fonte="coresso", tipo="terceiro", nome="Terceiro 1"
            ),
        ]

        total = persistir_extracao_staging(
            registros, id_execucao=str(id_execucao)
        )

        assert total == 3
        assert UsuarioServidorStaging.objects.count() == 1
        assert UsuarioAlunoStaging.objects.count() == 1
        assert UsuarioTerceiroStaging.objects.count() == 1

    def test_lista_vazia_retorna_zero(self) -> None:
        id_execucao = uuid.uuid4()
        assert (
            persistir_extracao_staging([], id_execucao=str(id_execucao)) == 0
        )

    def test_tipo_desconhecido_e_ignorado(self) -> None:
        id_execucao = uuid.uuid4()
        registros = [self._registro(tipo="desconhecido")]

        total = persistir_extracao_staging(
            registros, id_execucao=str(id_execucao)
        )

        assert total == 0
        assert UsuarioServidorStaging.objects.count() == 0
