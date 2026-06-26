"""Testes para o management command validar_e2e."""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

from apps.controle_etl.management.commands.validar_e2e import (
    _col_keycloak,
    _filtrar_usuarios,
    _identificadores_usuario,
    _info_usuario_kc,
    _linhas_amostra_por_fonte,
    _url_usuario_kc,
)
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

    def test_fonte_coresso_executa_apenas_coresso(self, tmp_path: Any) -> None:
        saida_md = tmp_path / "validacao.md"
        admin = _fake_admin_kc({"11122233344"})

        with (
            self._patch_pipeline(),
            self._patch_sistemas_perfis_vinculos(),
            patch(
                f"{_CMD}.obter_admin_keycloak",
                return_value=admin,
            ),
        ):
            out = StringIO()
            call_command(
                "validar_e2e",
                f"--saida={saida_md}",
                "--fonte=coresso",
                "--lote-maximo=5",
                stdout=out,
            )
        assert saida_md.exists()

    def test_forcar_atualizacao_sem_sis_id(self, tmp_path: Any) -> None:
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
                "--forcar-atualizacao",
                "--lote-maximo=5",
                stdout=StringIO(),
            )
        assert saida_md.exists()


# ------------------------------------------------------------------
# Helpers do validar_e2e
# ------------------------------------------------------------------


class TestUrlUsuarioKc:
    def test_com_id(self, settings: Any) -> None:
        settings.KEYCLOAK_URL_SERVIDOR = "https://kc/"
        url = _url_usuario_kc("abc-123", "sme-apps")
        assert "/users/abc-123/settings" in url

    def test_sem_id(self) -> None:
        assert _url_usuario_kc(None, "r") == "—"


class TestInfoUsuarioKc:
    def test_extrai_dados(self) -> None:
        u = {
            "id": "kc-1",
            "firstName": "Ana",
            "lastName": "Silva",
            "email": "a@x",
        }
        info = _info_usuario_kc(u)
        assert info["encontrado"] is True
        assert info["kc_user_id"] == "kc-1"
        assert info["nome"] == "Ana Silva"
        assert info["email"] == "a@x"


class TestColKeycloak:
    def test_encontrado_com_id(self, settings: Any) -> None:
        settings.KEYCLOAK_URL_SERVIDOR = "https://kc/"
        info = {"encontrado": True, "kc_user_id": "abc"}
        col = _col_keycloak(info, "sme")
        assert "✅ ver" in col
        assert "abc/settings" in col

    def test_encontrado_sem_id(self) -> None:
        info = {"encontrado": True, "kc_user_id": None}
        assert _col_keycloak(info, "r") == "✅"

    def test_nao_encontrado(self) -> None:
        info = {"encontrado": False}
        assert _col_keycloak(info, "r") == "❌"


class TestIdentificadoresUsuario:
    def test_com_cpf_rf_matricula(self) -> None:
        u = MagicMock()
        u.cpf = "11122233344"
        u.rf = "7777777"
        u.matricula = "9999"
        ids = _identificadores_usuario(u)
        assert ids == {"11122233344", "7777777", "9999"}

    def test_sem_dados(self) -> None:
        u = MagicMock()
        u.cpf = ""
        u.rf = None
        u.matricula = None
        assert _identificadores_usuario(u) == set()


@pytest.mark.django_db
class TestFiltrarUsuarios:
    def test_sem_filtro_retorna_todos(self) -> None:
        import uuid

        id_exec = str(uuid.uuid4())
        UsuarioServidorStaging.objects.create(
            id_execucao=id_exec,
            fonte="se1426",
            rf="111",
            nome="A",
        )
        result = _filtrar_usuarios(id_exec, UsuarioServidorStaging, None)
        assert len(result) == 1

    def test_filtra_por_login(self) -> None:
        import uuid

        id_exec = str(uuid.uuid4())
        UsuarioServidorStaging.objects.create(
            id_execucao=id_exec,
            fonte="se1426",
            rf="111",
            nome="A",
        )
        UsuarioServidorStaging.objects.create(
            id_execucao=id_exec,
            fonte="se1426",
            rf="222",
            nome="B",
        )
        result = _filtrar_usuarios(id_exec, UsuarioServidorStaging, {"111"})
        assert len(result) == 1
        assert result[0].rf == "111"


@pytest.mark.django_db
class TestLinhasAmostraPorFonte:
    def test_gera_linhas_md(self, settings: Any) -> None:
        import uuid

        settings.KEYCLOAK_URL_SERVIDOR = "https://kc/"
        id_exec = str(uuid.uuid4())
        u = UsuarioServidorStaging.objects.create(
            id_execucao=id_exec,
            fonte="se1426",
            rf="111",
            nome="Teste",
            situacao="pronto",
        )
        validacoes = {
            u.id: {
                "encontrado": True,
                "kc_user_id": "kc-1",
            }
        }
        linhas = _linhas_amostra_por_fonte(
            id_exec, "sme-apps", validacoes, None
        )
        texto = "\n".join(linhas)
        assert "111" in texto
        assert "Teste" in texto
        assert "✅" in texto

    def test_sem_usuarios_retorna_vazio(self) -> None:
        import uuid

        linhas = _linhas_amostra_por_fonte(str(uuid.uuid4()), "r", {}, None)
        assert linhas == []


# ------------------------------------------------------------------
# Métodos internos do Command (cobertura direta)
# ------------------------------------------------------------------


@pytest.mark.django_db
class TestMetodosInternosValidarE2e:
    """Testa métodos internos do Command diretamente."""

    def _cmd(self) -> Any:
        from apps.controle_etl.management.commands.validar_e2e import Command

        cmd = Command()
        cmd.stdout = StringIO()
        cmd.style = cmd.create_parser("", "validar_e2e").prog  # type: ignore
        # Mock do style para não quebrar
        cmd.style = MagicMock()
        cmd.style.SUCCESS = lambda x: x
        cmd.style.ERROR = lambda x: x
        return cmd

    def test_extrair_e_provisionar_sistemas(self) -> None:
        cmd = self._cmd()
        admin = MagicMock()
        from apps.staging.models import SistemaStaging

        SistemaStaging.objects.create(coresso_sis_id=1, nome="T", situacao=1)
        with (
            patch(
                "apps.extracao.tasks.extrair_sistemas_coresso",
                return_value=2,
            ),
            patch(f"{_CMD}.obter_admin_keycloak", return_value=admin),
            patch(
                "apps.controle_etl.orquestrador_kc" + ".provisionar_client_kc",
                return_value={"acao": "criado"},
            ),
        ):
            r = cmd._extrair_e_provisionar_sistemas("sme-apps")
        assert r["total_sistemas"] == 2
        assert r["clients_criados"] == 1

    def test_extrair_e_provisionar_perfis(self) -> None:
        cmd = self._cmd()
        admin = MagicMock()
        from apps.staging.models import PerfilCoressoStaging, SistemaStaging

        sis = SistemaStaging.objects.create(
            coresso_sis_id=10, nome="S", kc_client_uuid="u"
        )
        PerfilCoressoStaging.objects.create(
            coresso_gru_id="g10",
            coresso_sis_id=10,
            sistema=sis,
            nome="P",
            kc_role_nome="P",
        )
        with (
            patch(
                "apps.extracao.tasks.extrair_perfis_coresso",
                return_value=3,
            ),
            patch(f"{_CMD}.obter_admin_keycloak", return_value=admin),
            patch(
                "apps.controle_etl.orquestrador_kc"
                + ".provisionar_role_client_kc",
                return_value={"acao": "criado"},
            ),
        ):
            r = cmd._extrair_e_provisionar_perfis("sme-apps")
        assert r["total_perfis"] == 3
        assert r["roles_criados"] == 1

    def test_atribuir_vinculos(self) -> None:
        cmd = self._cmd()
        vinculos = [
            {
                "login": "111",
                "cpf": "",
                "nome": "A",
                "email": "a@x",
                "gru_id": "g",
                "gru_nome": "G1",
                "sis_id": 1,
            },
        ]
        admin = MagicMock()
        with (
            patch(f"{_CMD}.obter_admin_keycloak", return_value=admin),
            patch(
                "apps.extracao.tasks"
                + ".extrair_vinculos_usuario_grupo_coresso",
                return_value=iter(vinculos),
            ),
            patch(
                "apps.controle_etl.orquestrador_kc"
                + ".atribuir_client_roles_usuario_kc",
                return_value={
                    "atribuidos": 1,
                    "ignorados": 0,
                    "erros": 0,
                },
            ),
        ):
            resultado, usuarios = cmd._atribuir_vinculos("sme-apps", sis_id=1)
        assert resultado["atribuidos"] == 1
        assert "111" in usuarios
        assert usuarios["111"]["grupos"] == "G1"

    def test_criar_usuarios_ausentes_kc(self) -> None:
        cmd = self._cmd()
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-new"
        with patch(f"{_CMD}.obter_admin_keycloak", return_value=admin):
            cmd._criar_usuarios_ausentes_kc(
                "sme-apps",
                {"111": {"nome": "Joao", "email": "j@x", "cpf": ""}},
            )
        admin.create_user.assert_called_once()

    def test_validar_logins_keycloak_encontra(self) -> None:
        cmd = self._cmd()
        admin = MagicMock()
        admin.get_users.return_value = [
            {"id": "kc-1", "firstName": "A", "lastName": "B", "email": "a@x"}
        ]
        with patch(f"{_CMD}.obter_admin_keycloak", return_value=admin):
            r = cmd._validar_logins_keycloak("sme-apps", {"111"})
        assert r["111"]["encontrado"] is True

    def test_validar_logins_keycloak_nao_encontra(self) -> None:
        cmd = self._cmd()
        admin = MagicMock()
        admin.get_users.return_value = []
        with patch(f"{_CMD}.obter_admin_keycloak", return_value=admin):
            r = cmd._validar_logins_keycloak("sme-apps", {"999"})
        assert r["999"]["encontrado"] is False

    def test_buscar_usuario_kc_por_rf(self) -> None:
        cmd = self._cmd()
        admin = MagicMock()
        user = {"id": "kc-1", "username": "cpf123"}

        def get_users_side(query: dict) -> list:
            if "rf:" in str(query.get("q", "")):
                return [user]
            return []

        admin.get_users.side_effect = get_users_side
        result = cmd._buscar_usuario_kc(admin, "7777777")
        assert result is not None
        assert result["id"] == "kc-1"

    def test_provisionar_usuarios_do_sistema(self) -> None:
        import uuid

        cmd = self._cmd()
        id_exec = str(uuid.uuid4())
        UsuarioServidorStaging.objects.create(
            id_execucao=id_exec,
            fonte="se1426",
            rf="111",
            nome="A",
            situacao="pronto",
        )
        admin = MagicMock()
        with (
            patch(f"{_CMD}.obter_admin_keycloak", return_value=admin),
            patch(
                "apps.controle_etl.orquestrador_kc"
                + ".provisionar_usuario_kc",
                return_value={"acao": "criado"},
            ) as mock_prov,
        ):
            cmd._provisionar_usuarios_do_sistema(
                id_exec, "sme-apps", {"111"}, False
            )
        mock_prov.assert_called_once()

    def test_extrair_e_provisionar_usuarios(self) -> None:
        import uuid

        cmd = self._cmd()
        id_exec = str(uuid.uuid4())

        ExecucaoETL.objects.create(id_execucao=id_exec, fonte="coresso")

        with (
            patch(
                _CMD + ".task_identidade_extrair_coresso",
                return_value={"total_extraido": 0},
            ),
            patch(
                _CMD + ".task_identidade_resolver_identidade",
                return_value={"total_transformado": 0},
            ),
            patch(
                _CMD + ".task_provisionar_identidade_keycloak",
                return_value={"total_provisionado": 0},
            ),
        ):
            cmd._extrair_e_provisionar_usuarios(id_exec, "coresso")
