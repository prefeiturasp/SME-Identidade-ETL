"""Testes para o management command validar_e2e."""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

from apps.controle_etl.models import ExecucaoETL, LogEtapaETL
from apps.staging.models import (
    UsuarioAlunoStaging,
    UsuarioServidorStaging,
    UsuarioTerceiroStaging,
)


def _fake_admin_kc(
    encontrados: set[str] | None = None,
) -> MagicMock:
    """Cria um KeycloakAdmin fake que confirma os usernames informados."""
    admin = MagicMock()
    encontrados = encontrados or set()

    def _get_users(
        query: dict[str, Any],
    ) -> list[dict[str, str]]:
        username = query["username"]
        return [{"username": username}] if username in encontrados else []

    admin.get_users.side_effect = _get_users
    return admin


@pytest.mark.django_db
class TestValidarE2e:
    def _patch_pipeline(self, *, total_erros: int = 0) -> Any:
        """Substitui as tasks reais por fakes que populam o staging."""

        def _fake_se1426(
            id_execucao: str,
            data_referencia: str | None = None,
        ) -> dict[str, int]:
            UsuarioServidorStaging.objects.create(
                id_execucao=id_execucao,
                fonte="se1426",
                rf="1111111",
                nome="Servidor Um",
                situacao="carregado",
            )
            return {"total_extraido": 1}

        def _fake_coresso(
            id_execucao: str,
            data_referencia: str | None = None,
        ) -> dict[str, int]:
            UsuarioTerceiroStaging.objects.create(
                id_execucao=id_execucao,
                fonte="coresso",
                cpf="11122233344",
                nome="Terceiro Um",
                situacao="carregado",
            )
            return {"total_extraido": 1}

        def _fake_eol(
            id_execucao: str,
            data_referencia: str | None = None,
        ) -> dict[str, int]:
            UsuarioAlunoStaging.objects.create(
                id_execucao=id_execucao,
                fonte="eol_alunos",
                matricula="9999",
                nome="Aluno Um",
                situacao="carregado",
            )
            return {"total_extraido": 1}

        def _fake_resolver(
            resultados_extracao: Any, id_execucao: str
        ) -> dict[str, int]:
            execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
            LogEtapaETL.objects.create(
                execucao=execucao,
                nome_etapa=LogEtapaETL.NomeEtapa.RESOLVER_IDENTIDADE,
                ordem_etapa=3,
                situacao=LogEtapaETL.Situacao.SUCESSO,
                registros_entrada=3,
                registros_saida=3,
            )
            execucao.total_transformado = 3
            execucao.save(update_fields=["total_transformado"])
            return {"total_transformado": 3, "total_deduplicado": 3}

        def _fake_provisionar(
            resultado_resolucao: Any,
            id_execucao: str,
        ) -> dict[str, int]:
            execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
            LogEtapaETL.objects.create(
                execucao=execucao,
                nome_etapa=LogEtapaETL.NomeEtapa.PROVISIONAR_KEYCLOAK,
                ordem_etapa=4,
                situacao=LogEtapaETL.Situacao.SUCESSO,
                registros_entrada=3,
                registros_saida=3,
                registros_erro=total_erros,
            )
            execucao.total_carregado = 3
            execucao.total_erros = total_erros
            execucao.save(update_fields=["total_carregado", "total_erros"])
            return {"total_provisionado": 3, "total_erros": total_erros}

        return patch.multiple(
            "apps.controle_etl.management.commands.validar_e2e",
            task_identidade_extrair_se1426=_fake_se1426,
            task_identidade_extrair_coresso=_fake_coresso,
            task_identidade_extrair_eol_alunos=_fake_eol,
            task_identidade_resolver_identidade=_fake_resolver,
            task_provisionar_identidade_keycloak=_fake_provisionar,
        )

    def test_gera_relatorio_com_todos_confirmados(self, tmp_path: Any) -> None:
        saida_md = tmp_path / "validacao.md"
        admin = _fake_admin_kc({"1111111", "11122233344", "9999"})

        with (
            self._patch_pipeline(),
            patch(
                "apps.controle_etl.management.commands.validar_e2e"
                ".obter_admin_keycloak",
                return_value=admin,
            ),
        ):
            saida = StringIO()
            call_command(
                "validar_e2e",
                f"--saida={saida_md}",
                "--lote-maximo=5",
                stdout=saida,
            )

        texto = saida.getvalue()
        assert "situacao=sucesso" in texto
        assert "se1426: 1/1 confirmados no Keycloak" in texto
        assert "coresso: 1/1 confirmados no Keycloak" in texto
        assert "eol_alunos: 1/1 confirmados no Keycloak" in texto

        execucao = ExecucaoETL.objects.get()
        assert execucao.situacao == ExecucaoETL.Situacao.SUCESSO

        conteudo_md = saida_md.read_text(encoding="utf-8")
        assert "# Relatório de validação E2E" in conteudo_md
        assert "1111111" in conteudo_md
        assert "11122233344" in conteudo_md
        assert "9999" in conteudo_md
        assert conteudo_md.count("✅") == 3
        assert "❌" not in conteudo_md

    def test_marca_nao_confirmado_quando_ausente_no_keycloak(
        self, tmp_path: Any
    ) -> None:
        saida_md = tmp_path / "validacao.md"
        admin = _fake_admin_kc(set())  # nenhum usuário confirmado

        with (
            self._patch_pipeline(),
            patch(
                "apps.controle_etl.management.commands.validar_e2e"
                ".obter_admin_keycloak",
                return_value=admin,
            ),
        ):
            call_command(
                "validar_e2e",
                f"--saida={saida_md}",
                "--lote-maximo=5",
                stdout=StringIO(),
            )

        conteudo_md = saida_md.read_text(encoding="utf-8")
        assert conteudo_md.count("❌") == 3
        assert "✅" not in conteudo_md

    def test_situacao_falha_quando_ha_erros(self, tmp_path: Any) -> None:
        saida_md = tmp_path / "validacao.md"
        admin = _fake_admin_kc(set())

        with (
            self._patch_pipeline(total_erros=1),
            patch(
                "apps.controle_etl.management.commands.validar_e2e"
                ".obter_admin_keycloak",
                return_value=admin,
            ),
        ):
            call_command(
                "validar_e2e",
                f"--saida={saida_md}",
                "--lote-maximo=5",
                stdout=StringIO(),
            )

        execucao = ExecucaoETL.objects.get()
        assert execucao.situacao == ExecucaoETL.Situacao.FALHA

    def test_chunk_size_sobrepoe_setting(
        self, settings: Any, tmp_path: Any
    ) -> None:
        saida_md = tmp_path / "validacao.md"
        admin = _fake_admin_kc(set())
        settings.ETL_CHUNK_SIZE = 50000

        with (
            self._patch_pipeline(),
            patch(
                "apps.controle_etl.management.commands.validar_e2e"
                ".obter_admin_keycloak",
                return_value=admin,
            ),
        ):
            call_command(
                "validar_e2e",
                f"--saida={saida_md}",
                "--chunk-size=20",
                stdout=StringIO(),
            )

        assert settings.ETL_CHUNK_SIZE == 20

    def test_realm_e_lote_maximo_repassados(
        self, settings: Any, tmp_path: Any
    ) -> None:
        saida_md = tmp_path / "validacao.md"
        admin = _fake_admin_kc(set())

        with (
            self._patch_pipeline(),
            patch(
                "apps.controle_etl.management.commands.validar_e2e"
                ".obter_admin_keycloak",
                return_value=admin,
            ) as mock_obter_admin,
        ):
            call_command(
                "validar_e2e",
                f"--saida={saida_md}",
                "--lote-maximo=7",
                "--realm=sme-hom",
                stdout=StringIO(),
            )

        assert settings.ETL_LOTE_MAXIMO == 7
        mock_obter_admin.assert_called_with(realm="sme-hom")
        execucao = ExecucaoETL.objects.get()
        assert execucao.realm_destino == "sme-hom"
