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

_CMD = "apps.controle_etl.management.commands.validar_e2e"


def _fake_admin_kc(
    encontrados: set[str] | None = None,
) -> MagicMock:
    """KeycloakAdmin fake que confirma usernames."""
    admin = MagicMock()
    encontrados = encontrados or set()

    def _get_users(
        query: dict[str, Any],
    ) -> list[dict[str, str]]:
        username = query["username"]
        return (
            [{"username": username, "id": username}]
            if username in encontrados
            else []
        )

    admin.get_users.side_effect = _get_users
    return admin


def _noop_sistemas() -> int:
    return 0


def _noop_perfis() -> int:
    return 0


def _noop_vinculos() -> list[dict]:
    return iter([])  # type: ignore[return-value]


def _noop_provisionar_client(
    admin: Any, sistema: Any, realm: Any = None
) -> dict:
    return {"acao": "criado"}


def _noop_provisionar_role(admin: Any, perfil: Any) -> dict:
    return {"acao": "criado"}


def _noop_atribuir_roles(admin: Any, vinculos: Any) -> dict:
    return {
        "atribuidos": 0,
        "ignorados": 0,
        "erros": 0,
    }


@pytest.mark.django_db
class TestValidarE2e:
    def _patch_pipeline(self, *, total_erros: int = 0) -> Any:
        """Substitui tasks reais por fakes."""

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
            resultados_extracao: Any,
            id_execucao: str,
        ) -> dict[str, int]:
            execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
            LogEtapaETL.objects.create(
                execucao=execucao,
                nome_etapa=(LogEtapaETL.NomeEtapa.RESOLVER_IDENTIDADE),
                ordem_etapa=3,
                situacao=LogEtapaETL.Situacao.SUCESSO,
                registros_entrada=3,
                registros_saida=3,
            )
            execucao.total_transformado = 3
            execucao.save(update_fields=["total_transformado"])
            return {
                "total_transformado": 3,
                "total_deduplicado": 3,
            }

        def _fake_provisionar(
            resultado_resolucao: Any,
            id_execucao: str,
        ) -> dict[str, int]:
            execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
            LogEtapaETL.objects.create(
                execucao=execucao,
                nome_etapa=(LogEtapaETL.NomeEtapa.PROVISIONAR_KEYCLOAK),
                ordem_etapa=4,
                situacao=LogEtapaETL.Situacao.SUCESSO,
                registros_entrada=3,
                registros_saida=3,
                registros_erro=total_erros,
            )
            execucao.total_carregado = 3
            execucao.total_erros = total_erros
            execucao.save(
                update_fields=[
                    "total_carregado",
                    "total_erros",
                ]
            )
            return {
                "total_provisionado": 3,
                "total_erros": total_erros,
            }

        return patch.multiple(
            _CMD,
            task_identidade_extrair_se1426=_fake_se1426,
            task_identidade_extrair_coresso=_fake_coresso,
            task_identidade_extrair_eol_alunos=_fake_eol,
            task_identidade_resolver_identidade=(_fake_resolver),
            task_provisionar_identidade_keycloak=(_fake_provisionar),
        )

    def _patch_sistemas_perfis_vinculos(self) -> Any:
        """Mock das novas funções de sistemas/perfis."""
        return patch.multiple(
            f"{_CMD}.Command",
            _extrair_e_provisionar_sistemas=(
                lambda self, realm: {
                    "total_sistemas": 0,
                    "clients_criados": 0,
                    "clients_atualizados": 0,
                    "clients_erros": 0,
                }
            ),
            _extrair_e_provisionar_perfis=(
                lambda self, realm: {
                    "total_perfis": 0,
                    "roles_criados": 0,
                    "roles_atualizados": 0,
                    "roles_ignorados": 0,
                    "roles_erros": 0,
                }
            ),
            _atribuir_vinculos=(
                lambda self, realm, **kw: (
                    {
                        "atribuidos": 0,
                        "ignorados": 0,
                        "erros": 0,
                    },
                    {},
                )
            ),
        )

    def test_gera_relatorio_com_todos_confirmados(self, tmp_path: Any) -> None:
        saida_md = tmp_path / "validacao.md"
        admin = _fake_admin_kc({"1111111", "11122233344", "9999"})

        with (
            self._patch_pipeline(),
            self._patch_sistemas_perfis_vinculos(),
            patch(
                f"{_CMD}.obter_admin_keycloak",
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
        assert conteudo_md.count("✅") >= 3
        assert "❌" not in conteudo_md
        assert "## Sistemas e Clients" in conteudo_md
        assert "## Perfis e Client Roles" in conteudo_md
        assert "## Vínculos" in conteudo_md
        assert "ver](" in conteudo_md

    def test_marca_nao_confirmado_quando_ausente_no_keycloak(
        self, tmp_path: Any
    ) -> None:
        saida_md = tmp_path / "validacao.md"
        admin = _fake_admin_kc(set())

        with (
            self._patch_pipeline(),
            self._patch_sistemas_perfis_vinculos(),
            patch(
                f"{_CMD}.obter_admin_keycloak",
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
            self._patch_sistemas_perfis_vinculos(),
            patch(
                f"{_CMD}.obter_admin_keycloak",
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
            self._patch_sistemas_perfis_vinculos(),
            patch(
                f"{_CMD}.obter_admin_keycloak",
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
            self._patch_sistemas_perfis_vinculos(),
            patch(
                f"{_CMD}.obter_admin_keycloak",
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

    def _patch_sis_id_mode(self) -> Any:
        """Mock para modo --sis-id."""
        vinculos_result = (
            {
                "atribuidos": 2,
                "ignorados": 0,
                "erros": 0,
            },
            {
                "6913261": {
                    "nome": "Angela Nunes",
                    "email": "a@sme.sp",
                    "cpf": "",
                    "grupos": "ASCOM, CODAE",
                },
            },
        )
        return patch.multiple(
            f"{_CMD}.Command",
            _extrair_e_provisionar_sistemas=(
                lambda self, realm: {
                    "total_sistemas": 1,
                    "clients_criados": 1,
                    "clients_atualizados": 0,
                    "clients_erros": 0,
                }
            ),
            _extrair_e_provisionar_perfis=(
                lambda self, realm: {
                    "total_perfis": 2,
                    "roles_criados": 2,
                    "roles_atualizados": 0,
                    "roles_ignorados": 0,
                    "roles_erros": 0,
                }
            ),
            _atribuir_vinculos=(lambda self, realm, **kw: vinculos_result),
            _criar_usuarios_ausentes_kc=(lambda self, realm, usis: None),
            _validar_logins_keycloak=(
                lambda self, realm, logins: {
                    "6913261": {
                        "encontrado": True,
                        "kc_user_id": "kc-ang",
                        "nome": "Angela Nunes",
                        "email": "a@sme.sp",
                    },
                }
            ),
        )

    def test_modo_sis_id_gera_relatorio(self, tmp_path: Any) -> None:
        saida_md = tmp_path / "validacao.md"

        with (
            self._patch_sis_id_mode(),
            patch(
                f"{_CMD}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
        ):
            saida = StringIO()
            call_command(
                "validar_e2e",
                f"--saida={saida_md}",
                "--sis-id=1008",
                stdout=saida,
            )

        texto = saida.getvalue()
        assert "situacao=sucesso" in texto

        conteudo = saida_md.read_text(encoding="utf-8")
        assert "## Usuários do sistema" in conteudo
        assert "6913261" in conteudo
        assert "Angela" in conteudo
        assert "ASCOM, CODAE" in conteudo
        assert "✅" in conteudo
        assert "sis_id=1008" in conteudo
