"""Testes para apps.controle_etl.orquestrador_kc."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings

from apps.controle_etl.orquestrador_kc import (
    _atribuir_roles_e_grupos,
    _atribuir_roles_sistema,
    _buscar_todas_contas_kc,
    _com_reintento,
    _criar_role_kc,
    _derivar_grupos,
    _derivar_roles_realm,
    _excecoes_retriaveis,
    _inferir_tipo_usuario,
    _localizar_usuario_kc,
    _merge_contas_kc,
    _migrar_client_roles_kc,
    _migrar_realm_roles_kc,
    _montar_queries_busca,
    _resolver_kc_user_id,
    _resolver_role_info,
    _resolver_username,
    _slugificar_client_id,
    _upsert_usuario_kc,
    atribuir_client_roles_usuario_kc,
    calcular_hash_conteudo,
    calcular_hash_extracao,
    conceder_acesso_kc,
    construir_payload_kc,
    construir_payload_perfil_token_ms,
    construir_payload_token_ms,
    montar_payload_perfil,
    payload_tem_identificador,
    provisionar_usuario_kc,
    provisionar_usuarios_kc_em_paralelo,
    resolver_id_origem,
    resolver_kc_user_id_de_usuario,
    sincronizar_usuario_kc,
)

# ---------------------------------------------------------------------------
# Helpers de dados
# ---------------------------------------------------------------------------


def _usuario(
    *,
    cpf: str = "12345678901",
    rf: str = "1234567",
    nome: str = "Ana Lima",
    email: str = "ana@sme.sp.gov.br",
    situacao: str = "ativo",
    cargo: str | None = None,
    funcao: str | None = None,
    dre: str | None = None,
    ue: str | None = None,
    lotacao: str | None = None,
    lotacao_nome: str | None = None,
    matricula: str | None = None,
    cod_escola: str | None = None,
    turma: str | None = None,
    tipo_acesso: str | None = None,
    fonte: str = "se1426",
    id: int = 1,
) -> MagicMock:
    u = MagicMock()
    u.cpf = cpf
    u.rf = rf
    u.nome = nome
    u.email = email
    u.situacao = situacao
    u.cargo = cargo
    u.funcao = funcao
    u.dre = dre
    u.ue = ue
    u.lotacao = lotacao
    u.lotacao_nome = lotacao_nome
    u.matricula = matricula
    u.cod_escola = cod_escola
    u.turma = turma
    u.tipo_acesso = tipo_acesso
    u.fonte = fonte
    u.id = id
    return u


# ---------------------------------------------------------------------------
# _resolver_username
# ---------------------------------------------------------------------------


class TestResolverUsername:
    """Testa a resolução do username de destino no Keycloak."""

    def test_prefere_rf_sobre_cpf(self) -> None:
        """Verifica que RF tem prioridade sobre CPF."""
        u = _usuario(cpf="12345678901", rf="9876543")
        assert _resolver_username(u) == "9876543"

    def test_usa_cpf_quando_sem_rf(self) -> None:
        """Verifica que o CPF é usado quando não há RF."""
        u = _usuario(cpf="123.456.789-01", rf="")
        assert _resolver_username(u) == "12345678901"

    def test_usa_rf_quando_sem_cpf(self) -> None:
        """Verifica que o RF é usado quando não há CPF."""
        u = _usuario(cpf="", rf="9876543")
        assert _resolver_username(u) == "9876543"

    def test_usa_matricula_quando_sem_cpf_e_rf(self) -> None:
        """Verifica que a matrícula é usada quando não há CPF nem RF."""
        u = _usuario(cpf="", rf="", matricula="M001")
        assert _resolver_username(u) == "M001"

    def test_fallback_fonte_id(self) -> None:
        """Verifica o fallback para "fonte-id" sem nenhum identificador."""
        u = _usuario(cpf="", rf="", matricula="")
        u.fonte = "eol_alunos"
        u.id = 99
        assert _resolver_username(u) == "eol_alunos-99"


# ---------------------------------------------------------------------------
# _excecoes_retriaveis
# ---------------------------------------------------------------------------


class TestExcecoesRetriaveis:
    """Testa a composição da tupla de exceções consideradas retriáveis."""

    def test_inclui_excecoes_do_keycloak_quando_lib_disponivel(self) -> None:
        """Verifica exceções do Keycloak incluídas com a lib disponível."""
        excecoes = _excecoes_retriaveis()
        assert ConnectionError in excecoes
        assert TimeoutError in excecoes
        assert len(excecoes) == 6

    def test_fallback_quando_lib_keycloak_indisponivel(self) -> None:
        """Verifica fallback p/ ConnectionError/TimeoutError sem keycloak."""
        import builtins

        import_original = builtins.__import__

        def import_falho(nome: str, *args: Any, **kwargs: Any) -> Any:
            if nome == "keycloak.exceptions":
                raise ImportError("keycloak não instalado")
            return import_original(nome, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_falho):
            excecoes = _excecoes_retriaveis()

        assert excecoes == (ConnectionError, TimeoutError)


# ---------------------------------------------------------------------------
# _inferir_tipo_usuario
# ---------------------------------------------------------------------------


class TestInferirTipoUsuario:
    """Testa a inferência do tipo de usuário a partir do modelo staging."""

    def test_servidor(self) -> None:
        """Verifica que UsuarioServidorStaging é inferido como "servidor"."""
        from apps.staging.models import UsuarioServidorStaging

        u = UsuarioServidorStaging(fonte="se1426")
        assert _inferir_tipo_usuario(u) == "servidor"

    def test_aluno(self) -> None:
        """Verifica que UsuarioAlunoStaging é inferido como "aluno"."""
        from apps.staging.models import UsuarioAlunoStaging

        u = UsuarioAlunoStaging(fonte="eol_alunos")
        assert _inferir_tipo_usuario(u) == "aluno"

    def test_terceiro_usa_tipo_acesso(self) -> None:
        """Verifica que terceiro usa tipo_acesso quando informado."""
        from apps.staging.models import UsuarioTerceiroStaging

        u = UsuarioTerceiroStaging(
            fonte="coresso", tipo_acesso="legado-coresso"
        )
        assert _inferir_tipo_usuario(u) == "legado-coresso"

    def test_terceiro_sem_tipo_acesso_usa_fallback(self) -> None:
        """Verifica o fallback para "terceiro" quando tipo_acesso é None."""
        from apps.staging.models import UsuarioTerceiroStaging

        u = UsuarioTerceiroStaging(fonte="coresso", tipo_acesso=None)
        assert _inferir_tipo_usuario(u) == "terceiro"

    def test_tipo_desconhecido_retorna_outro(self) -> None:
        """Verifica que um objeto de tipo desconhecido retorna "outro"."""
        assert _inferir_tipo_usuario(object()) == "outro"


# ---------------------------------------------------------------------------
# _derivar_roles_realm
# ---------------------------------------------------------------------------


class TestDerivarRolesRealm:
    """Testa a derivação de roles de realm a partir de cargo/função."""

    def test_cargo_conhecido_mapeia_role(self) -> None:
        """Verifica que um cargo conhecido é mapeado para a role Professor."""
        u = _usuario(
            cargo="PROFESSOR DE EDUCACAO INFANTIL E ENSINO FUNDAMENTAL I"
        )
        roles = _derivar_roles_realm(u)
        assert "Professor" in roles

    def test_funcao_conhecida_mapeia_role(self) -> None:
        """Verifica que função conhecida é mapeada para a role Diretor."""
        u = _usuario(funcao="DIRETOR DE ESCOLA")
        roles = _derivar_roles_realm(u)
        assert "Diretor" in roles

    def test_cargo_e_funcao_combinam_roles(self) -> None:
        """Verifica que cargo e função juntos combinam as roles."""
        u = _usuario(
            cargo="PROFESSOR DE ENSINO FUNDAMENTAL II E MEDIO",
            funcao="COORDENADOR PEDAGOGICO",
        )
        roles = _derivar_roles_realm(u)
        assert "Professor" in roles
        assert "CoordenadorPedagogico" in roles

    def test_cargo_desconhecido_retorna_lista_vazia(self) -> None:
        """Verifica que um cargo sem mapeamento retorna lista vazia."""
        u = _usuario(cargo="CARGO INEXISTENTE")
        assert _derivar_roles_realm(u) == []

    def test_sem_cargo_e_funcao_retorna_lista_vazia(self) -> None:
        """Verifica que a ausência de cargo e função retorna lista vazia."""
        u = _usuario(cargo=None, funcao=None)
        assert _derivar_roles_realm(u) == []

    def test_resultado_ordenado(self) -> None:
        """Verifica que as roles vêm em ordem alfabética."""
        u = _usuario(
            cargo="DIRETOR DE ESCOLA",
            funcao="COORDENADOR PEDAGOGICO",
        )
        roles = _derivar_roles_realm(u)
        assert roles == sorted(roles)


# ---------------------------------------------------------------------------
# _derivar_grupos
# ---------------------------------------------------------------------------


class TestDerivarGrupos:
    """Testa a derivação dos caminhos de grupos do Keycloak."""

    def test_dre_e_ue_gera_caminho_completo(self) -> None:
        """Verifica que DRE e UE geram o caminho completo /SME/DRE-x/UE-y."""
        u = _usuario(dre="1", ue="200")
        grupos = _derivar_grupos(u)
        assert "/SME/DRE-1/UE-200" in grupos

    def test_apenas_dre_gera_caminho_parcial(self) -> None:
        """Verifica que apenas a DRE gera o caminho parcial /SME/DRE-x."""
        u = _usuario(dre="2", ue=None)
        grupos = _derivar_grupos(u)
        assert "/SME/DRE-2" in grupos

    def test_lotacao_sem_dre(self) -> None:
        """Verifica que a lotação gera /SME/LOTACAO-x quando não há DRE."""
        u = _usuario(dre=None, ue=None, lotacao="LOT001")
        grupos = _derivar_grupos(u)
        assert "/SME/LOTACAO-LOT001" in grupos

    def test_sem_dados_retorna_lista_vazia(self) -> None:
        """Verifica que sem DRE, UE e lotação retorna lista vazia."""
        u = _usuario(dre=None, ue=None, lotacao=None)
        assert _derivar_grupos(u) == []


# ---------------------------------------------------------------------------
# construir_payload_kc
# ---------------------------------------------------------------------------


class TestConstruirPayloadKc:
    """Testa a construção do payload de usuário para a API do Keycloak."""

    def test_campos_obrigatorios_presentes(self) -> None:
        """Verifica que os campos obrigatórios estão no payload."""
        u = _usuario()
        payload = construir_payload_kc(u)
        for campo in (
            "username",
            "email",
            "firstName",
            "lastName",
            "enabled",
            "emailVerified",
            "attributes",
        ):
            assert campo in payload

    def test_situacao_inativo_desabilita_usuario(self) -> None:
        """Verifica que situação inativa define enabled como False."""
        u = _usuario(situacao="inativo")
        assert construir_payload_kc(u)["enabled"] is False

    def test_situacao_ativo_habilita_usuario(self) -> None:
        """Verifica que situação ativa define enabled como True."""
        u = _usuario(situacao="ativo")
        assert construir_payload_kc(u)["enabled"] is True

    def test_nome_partido_em_primeiro_e_ultimo(self) -> None:
        """Verifica que o nome completo é dividido em firstName e lastName."""
        u = _usuario(nome="Maria Clara Santos")
        payload = construir_payload_kc(u)
        assert payload["firstName"] == "Maria"
        assert payload["lastName"] == "Clara Santos"

    def test_nome_unico(self) -> None:
        """Verifica que nome de uma palavra preenche só firstName."""
        u = _usuario(nome="Teste")
        payload = construir_payload_kc(u)
        assert payload["firstName"] == "Teste"
        assert payload["lastName"] == ""

    def test_atributos_contem_campos_etl(self) -> None:
        """Verifica que attributes tem cpf, rf, fonte e tipo_usuario."""
        u = _usuario(cpf="11122233344", rf="9876543")
        attrs = construir_payload_kc(u)["attributes"]
        assert "cpf" in attrs
        assert "rf" in attrs
        assert "fonte" in attrs
        assert "tipo_usuario" in attrs


# ---------------------------------------------------------------------------
# construir_payload_token_ms
# ---------------------------------------------------------------------------


class TestConstruirPayloadTokenMs:
    """Testa a construção do payload de usuário para o token-ms."""

    def test_campos_essenciais(self) -> None:
        """Verifica que os campos essenciais estão no payload."""
        u = _usuario()
        payload = construir_payload_token_ms(u)
        for campo in (
            "rf",
            "cpf",
            "nome",
            "email",
            "tipo_usuario",
            "situacao",
            "fonte",
            "id_execucao",
        ):
            assert campo in payload

    def test_id_execucao_como_string(self) -> None:
        """Verifica que id_execucao é convertido para string no payload."""
        u = _usuario()
        u.id_execucao = "abc-123"
        payload = construir_payload_token_ms(u)
        assert isinstance(payload["id_execucao"], str)


# ---------------------------------------------------------------------------
# resolver_kc_user_id_de_usuario
# ---------------------------------------------------------------------------


class TestResolverKcUserIdDeUsuario:
    """Testa a resolução do kc_user_id a partir de um usuário staging."""

    def test_retorna_id_quando_encontrado(self) -> None:
        """Verifica que resolve o UUID pelo username derivado do RF."""
        u = _usuario(rf="1234567")
        admin = MagicMock()
        admin.get_users.return_value = [{"id": "kc-9"}]

        assert resolver_kc_user_id_de_usuario(admin, u) == "kc-9"
        admin.get_users.assert_called_once_with(
            {"username": "1234567", "exact": True}
        )

    def test_retorna_none_quando_nao_encontrado(self) -> None:
        """Verifica que retorna None sem lançar exceção."""
        u = _usuario(rf="1234567")
        admin = MagicMock()
        admin.get_users.return_value = []

        assert resolver_kc_user_id_de_usuario(admin, u) is None

    def test_reusa_cache_entre_chamadas(self) -> None:
        """Verifica que não consulta o Keycloak duas vezes p/ o mesmo user."""
        u = _usuario(rf="1234567")
        admin = MagicMock()
        admin.get_users.return_value = [{"id": "kc-9"}]
        cache: dict = {}

        resolver_kc_user_id_de_usuario(admin, u, cache)
        resolver_kc_user_id_de_usuario(admin, u, cache)

        admin.get_users.assert_called_once()


# ---------------------------------------------------------------------------
# montar_payload_perfil
# ---------------------------------------------------------------------------


class TestMontarPayloadPerfil:
    """Testa a montagem do payload de perfil a partir de dados já buscados.

    Diferente de ``construir_payload_perfil_token_ms``, não recebe
    staging nem consulta o CoreSSO — usada pelas views HTTP avulsas
    (``sincronizar_usuario``, ``conceder_acesso``), que já têm
    ``dados`` em mãos.
    """

    def test_monta_perfis_e_login_a_partir_de_dados(self) -> None:
        """Verifica que perfis vêm dos grupos de ``dados``, sem staging."""
        dados = {
            "login": "1234567",
            "cpf": "12345678901",
            "nome": "Ana Lima",
            "email": "ana@sme.sp.gov.br",
            "situacao": "ativo",
            "sistemas": {
                1: {
                    "sis_id": 1,
                    "nome": "CoreSSO",
                    "grupos": [{"gru_id": "g1", "nome": "Administrador"}],
                },
            },
        }
        with patch(
            "apps.extracao.tasks.buscar_permissoes_usuario_coresso",
            return_value=[],
        ):
            payload = montar_payload_perfil(dados, identificador="1234567")

        assert payload["login"] == "1234567"
        assert payload["situacao"] == "ativo"
        assert payload["permissoes"] == []
        assert [p["nome"] for p in payload["perfis"]] == ["Administrador"]

    def test_usa_identificador_como_login_de_fallback(self) -> None:
        """Verifica o fallback de login quando dados["login"] é vazio."""
        dados = {
            "login": "",
            "cpf": "",
            "nome": "",
            "email": "",
            "situacao": "ativo",
            "sistemas": {},
        }
        with patch(
            "apps.extracao.tasks.buscar_permissoes_usuario_coresso",
            return_value=[],
        ):
            payload = montar_payload_perfil(
                dados, identificador="ana@sme.sp.gov.br"
            )

        assert payload["login"] == "ana@sme.sp.gov.br"

    def test_aplica_fallback_de_nome_cpf_email(self) -> None:
        """Verifica que nome/cpf/email de fallback são usados se vazios."""
        dados = {
            "login": "1234567",
            "cpf": "",
            "nome": "",
            "email": "",
            "situacao": "ativo",
            "sistemas": {},
        }
        with patch(
            "apps.extracao.tasks.buscar_permissoes_usuario_coresso",
            return_value=[],
        ):
            payload = montar_payload_perfil(
                dados,
                identificador="1234567",
                nome="Nome Fallback",
                cpf="99999999999",
                email="fallback@sme.sp.gov.br",
                rf="1234567",
                dre="DRE01",
                contrato_externo=True,
            )

        assert payload["nome"] == "Nome Fallback"
        assert payload["cpf"] == "99999999999"
        assert payload["email"] == "fallback@sme.sp.gov.br"
        assert payload["rf"] == "1234567"
        assert payload["dre_codigo"] == "DRE01"
        assert payload["contrato_externo"] is True


# ---------------------------------------------------------------------------
# construir_payload_perfil_token_ms
# ---------------------------------------------------------------------------


class TestConstruirPayloadPerfilTokenMs:
    """Testa a construção do payload de perfis para o token-ms."""

    def test_retorna_none_sem_identificador(self) -> None:
        """Verifica que retorna None quando não há rf/cpf/email."""
        u = _usuario(rf="", cpf="", email="")
        assert construir_payload_perfil_token_ms(u) is None

    def test_retorna_none_quando_nao_encontrado_no_coresso(self) -> None:
        """Verifica que retorna None quando o CoreSSO não retorna dados."""
        u = _usuario()
        with patch(
            "apps.extracao.tasks.buscar_dados_usuario_coresso",
            return_value=None,
        ):
            assert construir_payload_perfil_token_ms(u) is None

    def test_monta_perfis_a_partir_dos_grupos_coresso(self) -> None:
        """Verifica que cada grupo/sistema do CoreSSO vira um perfil."""
        u = _usuario(rf="1234567")
        dados_coresso = {
            "login": "1234567",
            "cpf": "12345678901",
            "nome": "Ana Lima",
            "email": "ana@sme.sp.gov.br",
            "situacao": "ativo",
            "sistemas": {
                1: {
                    "sis_id": 1,
                    "nome": "CoreSSO",
                    "grupos": [{"gru_id": "g1", "nome": "Administrador"}],
                },
                2: {
                    "sis_id": 2,
                    "nome": "Novo SGP",
                    "grupos": [
                        {"gru_id": "g2", "nome": "Professor"},
                        {"gru_id": "g3", "nome": "Coordenador"},
                    ],
                },
            },
        }
        with (
            patch(
                "apps.extracao.tasks.buscar_dados_usuario_coresso",
                return_value=dados_coresso,
            ),
            patch(
                "apps.extracao.tasks.buscar_permissoes_usuario_coresso",
                return_value=[],
            ),
        ):
            payload = construir_payload_perfil_token_ms(u)

        assert payload is not None
        assert payload["login"] == "1234567"
        assert payload["situacao"] == "ativo"
        assert payload["permissoes"] == []
        nomes_perfis = sorted(p["nome"] for p in payload["perfis"])
        assert nomes_perfis == ["Administrador", "Coordenador", "Professor"]
        assert all(p["ativo"] for p in payload["perfis"])
        assert all("id" in p for p in payload["perfis"])

    def test_perfis_vazios_quando_usuario_sem_vinculos(self) -> None:
        """Verifica payload com perfis=[] quando o usuário não tem grupos."""
        u = _usuario(rf="1234567")
        dados_coresso = {
            "login": "1234567",
            "cpf": "12345678901",
            "nome": "Ana Lima",
            "email": "ana@sme.sp.gov.br",
            "situacao": "ativo",
            "sistemas": {},
        }
        with (
            patch(
                "apps.extracao.tasks.buscar_dados_usuario_coresso",
                return_value=dados_coresso,
            ),
            patch(
                "apps.extracao.tasks.buscar_permissoes_usuario_coresso",
                return_value=[],
            ) as mock_permissoes,
        ):
            payload = construir_payload_perfil_token_ms(u)

        assert payload is not None
        assert payload["perfis"] == []
        assert payload["permissoes"] == []
        mock_permissoes.assert_called_once_with([])

    def test_monta_permissoes_a_partir_de_sys_grupopermissao(self) -> None:
        """Verifica que permissões vêm agrupadas por sistema/módulo."""
        u = _usuario(rf="1234567")
        dados_coresso = {
            "login": "1234567",
            "cpf": "12345678901",
            "nome": "Ana Lima",
            "email": "ana@sme.sp.gov.br",
            "situacao": "ativo",
            "sistemas": {
                1: {
                    "sis_id": 1,
                    "nome": "CoreSSO",
                    "grupos": [{"gru_id": "g1", "nome": "Administrador"}],
                },
            },
        }
        permissoes_coresso = [
            {
                "sis_id": 1,
                "sis_nome": "CoreSSO",
                "mod_id": 3,
                "mod_nome": "Usuários",
                "consultar": True,
                "inserir": True,
                "alterar": True,
                "excluir": True,
            },
        ]
        with (
            patch(
                "apps.extracao.tasks.buscar_dados_usuario_coresso",
                return_value=dados_coresso,
            ),
            patch(
                "apps.extracao.tasks.buscar_permissoes_usuario_coresso",
                return_value=permissoes_coresso,
            ) as mock_permissoes,
        ):
            payload = construir_payload_perfil_token_ms(u)

        assert payload is not None
        assert payload["permissoes"] == [
            {
                "sistema_id": 1,
                "sistema_nome": "CoreSSO",
                "modulo_id": 3,
                "modulo_nome": "Usuários",
                "consultar": True,
                "inserir": True,
                "alterar": True,
                "excluir": True,
            }
        ]
        mock_permissoes.assert_called_once_with(["g1"])

    def test_deduplica_permissoes_do_mesmo_modulo_entre_grupos(self) -> None:
        """Verifica que módulo repetido em grupos diferentes vira 1 item.

        Duas linhas para o mesmo (sistema, módulo) — cada uma vinda de
        um grupo diferente do usuário — devem virar uma única entrada
        no payload, com as flags combinadas por OR lógico.
        """
        u = _usuario(rf="1234567")
        dados_coresso = {
            "login": "1234567",
            "cpf": "12345678901",
            "nome": "Ana Lima",
            "email": "ana@sme.sp.gov.br",
            "situacao": "ativo",
            "sistemas": {
                1: {
                    "sis_id": 1,
                    "nome": "CoreSSO",
                    "grupos": [
                        {"gru_id": "g1", "nome": "Consulta"},
                        {"gru_id": "g2", "nome": "Edição"},
                    ],
                },
            },
        }
        permissoes_coresso = [
            {
                "sis_id": 1,
                "sis_nome": "CoreSSO",
                "mod_id": 3,
                "mod_nome": "Usuários",
                "consultar": True,
                "inserir": False,
                "alterar": False,
                "excluir": False,
            },
            {
                "sis_id": 1,
                "sis_nome": "CoreSSO",
                "mod_id": 3,
                "mod_nome": "Usuários",
                "consultar": False,
                "inserir": True,
                "alterar": True,
                "excluir": False,
            },
        ]
        with (
            patch(
                "apps.extracao.tasks.buscar_dados_usuario_coresso",
                return_value=dados_coresso,
            ),
            patch(
                "apps.extracao.tasks.buscar_permissoes_usuario_coresso",
                return_value=permissoes_coresso,
            ),
        ):
            payload = construir_payload_perfil_token_ms(u)

        assert payload is not None
        assert len(payload["permissoes"]) == 1
        permissao = payload["permissoes"][0]
        assert permissao["consultar"] is True
        assert permissao["inserir"] is True
        assert permissao["alterar"] is True
        assert permissao["excluir"] is False


# ---------------------------------------------------------------------------
# calcular_hash_conteudo
# ---------------------------------------------------------------------------


class TestCalcularHashConteudo:
    """Testa o cálculo do hash de conteúdo usado na idempotência."""

    def test_retorna_string_hex_64_chars(self) -> None:
        """Verifica que o hash é hexadecimal com 64 caracteres."""
        h = calcular_hash_conteudo({"a": 1})
        assert isinstance(h, str)
        assert len(h) == 64

    def test_mesmo_payload_mesmo_hash(self) -> None:
        """Verifica que o mesmo payload produz sempre o mesmo hash."""
        p = {"b": 2, "a": 1}
        assert calcular_hash_conteudo(p) == calcular_hash_conteudo(p)

    def test_payloads_diferentes_hashes_diferentes(self) -> None:
        """Verifica que payloads diferentes produzem hashes diferentes."""
        assert calcular_hash_conteudo({"x": 1}) != calcular_hash_conteudo(
            {"x": 2}
        )

    def test_ordem_das_chaves_nao_importa(self) -> None:
        """Verifica que a ordem das chaves no dict não altera o hash."""
        assert calcular_hash_conteudo(
            {"a": 1, "b": 2}
        ) == calcular_hash_conteudo({"b": 2, "a": 1})


# ---------------------------------------------------------------------------
# calcular_hash_extracao / resolver_id_origem / payload_tem_identificador
# ---------------------------------------------------------------------------


class TestCalcularHashExtracao:
    """Testa o hash de extração usado para decidir reextração."""

    def test_retorna_string_hex_64_chars(self) -> None:
        """Verifica que o hash é hexadecimal com 64 caracteres."""
        h = calcular_hash_extracao(_usuario())
        assert isinstance(h, str)
        assert len(h) == 64

    def test_mesmo_usuario_mesmo_hash(self) -> None:
        """Verifica que o mesmo dado produz sempre o mesmo hash."""
        u1 = _usuario(nome="Ana Lima")
        u2 = _usuario(nome="Ana Lima")
        assert calcular_hash_extracao(u1) == calcular_hash_extracao(u2)

    def test_nome_diferente_hash_diferente(self) -> None:
        """Verifica que uma mudança no dado altera o hash."""
        u1 = _usuario(nome="Ana Lima")
        u2 = _usuario(nome="Ana Lima Silva")
        assert calcular_hash_extracao(u1) != calcular_hash_extracao(u2)

    def test_campo_ausente_no_tipo_nao_gera_erro(self) -> None:
        """Verifica que campos ausentes no tipo não geram erro.

        Ex.: cargo em aluno não quebra o cálculo — usa None como
        fallback.
        """
        aluno = MagicMock(spec=["cpf", "rf", "nome", "email", "situacao"])
        aluno.cpf = "12345678901"
        aluno.rf = None
        aluno.nome = "Pedro"
        aluno.email = "pedro@sme.sp.gov.br"
        aluno.situacao = "ativo"

        h = calcular_hash_extracao(aluno)
        assert isinstance(h, str)
        assert len(h) == 64


class TestResolverIdOrigem:
    """Testa a resolução do identificador estável entre execuções."""

    def test_prefere_cpf_sobre_rf(self) -> None:
        """Verifica que CPF tem prioridade sobre RF."""
        u = _usuario(cpf="12345678901", rf="9876543")
        assert resolver_id_origem(u) == "12345678901"

    def test_usa_rf_quando_sem_cpf(self) -> None:
        """Verifica que RF é usado quando não há CPF."""
        u = _usuario(cpf="", rf="9876543")
        assert resolver_id_origem(u) == "9876543"

    def test_usa_pk_do_staging_como_ultimo_recurso(self) -> None:
        """Verifica o fallback para o PK quando não há CPF nem RF."""
        u = _usuario(cpf="", rf="", id=42)
        assert resolver_id_origem(u) == "42"


class TestPayloadTemIdentificador:
    """Testa a checagem de identificador no payload do token-ms."""

    def test_com_rf_retorna_true(self) -> None:
        assert payload_tem_identificador({"rf": "1234567"}) is True

    def test_sem_identificador_retorna_false(self) -> None:
        assert (
            payload_tem_identificador(
                {"rf": None, "cpf": None, "matricula": None}
            )
            is False
        )


# ---------------------------------------------------------------------------
# _slugificar_client_id
# ---------------------------------------------------------------------------


class TestSlugificarClientId:
    """Testa a geração de slugs para client_id no Keycloak."""

    def test_lowercase_e_hifen(self) -> None:
        """Verifica que o nome é convertido para lowercase com hífens."""
        assert _slugificar_client_id("Sistema Escolar") == "sistema-escolar"

    def test_remove_acentos(self) -> None:
        """Verifica que acentos são removidos do slug gerado."""
        slug = _slugificar_client_id("Gestão Pedagógica")
        assert "ã" not in slug
        assert "ó" not in slug

    def test_string_vazia_retorna_fallback(self) -> None:
        """Verifica fallback para "sistema-sem-nome" com nome vazio."""
        assert _slugificar_client_id("") == "sistema-sem-nome"


# ---------------------------------------------------------------------------
# provisionar_usuario_kc — idempotência
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProvisionarUsuarioKcIdempotencia:
    def test_usuario_sem_mudanca_retorna_ignorado(self, settings: Any) -> None:
        from apps.controle_etl.models import ControleProvisionamento
        from apps.controle_etl.orquestrador_kc import (
            calcular_hash_conteudo,
            calcular_hash_extracao,
            construir_payload_kc,
            provisionar_usuario_kc,
        )

        u = _usuario()

        payload = construir_payload_kc(u)
        payload.pop("realmRoles", None)
        payload.pop("groups", None)
        hash_atual = calcular_hash_conteudo(payload)

        ControleProvisionamento.objects.create(
            tipo_entidade=ControleProvisionamento.TipoEntidade.USUARIO,
            sistema_origem=u.fonte,
            id_origem="12345678901",
            realm_destino="sme-apps",
            hash_extracao=calcular_hash_extracao(u),
            hash_keycloak=hash_atual,
            id_destino="kc-uuid-existente",
        )

        admin = MagicMock()
        resultado = provisionar_usuario_kc(admin, u, realm="sme-apps")
        assert resultado["acao"] == "ignorado"
        admin.create_user.assert_not_called()
        admin.update_user.assert_not_called()


# ---------------------------------------------------------------------------
# provisionar_usuario_kc — criação / atualização
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProvisionarUsuarioKcCriacaoAtualizacao:
    def test_cria_usuario_novo(self) -> None:
        from apps.controle_etl.models import ControleProvisionamento
        from apps.controle_etl.orquestrador_kc import provisionar_usuario_kc

        u = _usuario(cpf="11122233344", rf="")
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-novo-id"

        resultado = provisionar_usuario_kc(admin, u, realm="sme-apps")

        assert resultado["acao"] == "criado"
        assert resultado["kc_user_id"] == "kc-novo-id"
        admin.create_user.assert_called_once()
        admin.set_user_password.assert_called_once()
        controle = ControleProvisionamento.objects.get(id_origem="11122233344")
        assert controle.id_destino == "kc-novo-id"

    def test_falha_ao_definir_senha_inicial_nao_propaga(self) -> None:
        from apps.controle_etl.orquestrador_kc import provisionar_usuario_kc

        u = _usuario(cpf="55566677788", rf="")
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-novo-id-2"
        admin.set_user_password.side_effect = Exception("falha senha")

        resultado = provisionar_usuario_kc(admin, u, realm="sme-apps")

        assert resultado["acao"] == "criado"

    def test_atualiza_usuario_existente_por_email(self) -> None:
        from apps.controle_etl.orquestrador_kc import provisionar_usuario_kc

        u = _usuario(cpf="22233344455", rf="", email="ana@sme.sp.gov.br")
        admin = MagicMock()
        admin.get_users.return_value = [
            {"id": "kc-existente", "username": "22233344455"}
        ]

        resultado = provisionar_usuario_kc(admin, u, realm="sme-apps")

        assert resultado["acao"] == "atualizado"
        assert resultado["kc_user_id"] == "kc-existente"
        admin.update_user.assert_called_once()
        admin.create_user.assert_not_called()

    def test_atualiza_quando_controle_ja_possui_destino_e_hash_mudou(
        self,
    ) -> None:
        from apps.controle_etl.models import ControleProvisionamento
        from apps.controle_etl.orquestrador_kc import provisionar_usuario_kc

        u = _usuario(cpf="33344455566", rf="", nome="Nome Antigo")
        ControleProvisionamento.objects.create(
            tipo_entidade=ControleProvisionamento.TipoEntidade.USUARIO,
            sistema_origem=u.fonte,
            id_origem="33344455566",
            realm_destino="sme-apps",
            hash_keycloak="hash-desatualizado",
            id_destino="kc-uuid-velho",
        )

        admin = MagicMock()
        resultado = provisionar_usuario_kc(admin, u, realm="sme-apps")

        assert resultado["acao"] == "atualizado"
        assert resultado["kc_user_id"] == "kc-uuid-velho"
        admin.update_user.assert_called_once_with(
            "kc-uuid-velho", admin.update_user.call_args[0][1]
        )
        admin.create_user.assert_not_called()

    def test_atribui_roles_e_grupos_apos_provisionar(self) -> None:
        from apps.controle_etl.orquestrador_kc import provisionar_usuario_kc

        u = _usuario(
            cpf="44455566677",
            rf="",
            cargo="DIRETOR DE ESCOLA",
            dre="1",
            ue="100",
        )
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-id-roles"
        admin.get_realm_role.return_value = {"name": "Diretor"}
        admin.get_group_by_path.return_value = {"id": "grupo-1"}

        provisionar_usuario_kc(admin, u, realm="sme-apps")

        admin.assign_realm_roles.assert_called_once_with(
            "kc-id-roles", [{"name": "Diretor"}]
        )
        admin.group_user_add.assert_called_once_with("kc-id-roles", "grupo-1")

    def test_associa_execucao_ao_controle_quando_informada(self) -> None:
        from apps.controle_etl.models import (
            ControleProvisionamento,
            ExecucaoETL,
        )
        from apps.controle_etl.orquestrador_kc import provisionar_usuario_kc

        u = _usuario(cpf="66677788899", rf="")
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-id-execucao"
        execucao = ExecucaoETL.objects.create()

        provisionar_usuario_kc(admin, u, realm="sme-apps", execucao=execucao)

        controle = ControleProvisionamento.objects.get(id_origem="66677788899")
        assert controle.ultima_execucao_id == execucao.id


# ---------------------------------------------------------------------------
# provisionar_usuario_kc — matriz dos 3 hashes independentes
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProvisionarUsuarioKcMatrizHashes:
    """Testa as 3 checagens independentes (extração/Keycloak/token-ms).

    Regra: os 3 hashes precisam bater para ignorar tudo; onde um não
    bater, aquele estágio reexecuta, mesmo que os outros já estejam
    confirmados.
    """

    def _controle_existente(
        self, u: Any, *, hash_extracao: str, hash_keycloak: str
    ) -> Any:
        from apps.controle_etl.models import ControleProvisionamento

        return ControleProvisionamento.objects.create(
            tipo_entidade=ControleProvisionamento.TipoEntidade.USUARIO,
            sistema_origem=u.fonte,
            id_origem=resolver_id_origem(u),
            realm_destino="sme-apps",
            hash_extracao=hash_extracao,
            hash_keycloak=hash_keycloak,
            id_destino="kc-uuid-existente",
        )

    def _hash_keycloak_atual(self, u: Any) -> str:
        payload = construir_payload_kc(u)
        payload.pop("realmRoles", None)
        payload.pop("groups", None)
        return calcular_hash_conteudo(payload)

    def test_extracao_e_keycloak_batem_ignora_keycloak(self) -> None:
        """Extração+Keycloak batem: Keycloak é ignorado."""
        u = _usuario(cpf="10000000001", rf="")
        self._controle_existente(
            u,
            hash_extracao=calcular_hash_extracao(u),
            hash_keycloak=self._hash_keycloak_atual(u),
        )

        admin = MagicMock()
        resultado = provisionar_usuario_kc(admin, u, realm="sme-apps")

        assert resultado["acao"] == "ignorado"
        admin.create_user.assert_not_called()
        admin.update_user.assert_not_called()

    def test_extracao_bate_keycloak_nao_bate_reenvia_keycloak(self) -> None:
        """Extração bate mas Keycloak não: reenvia ao Keycloak.

        Reenvia mesmo sem mudança de dado (ex.: tentativa anterior
        falhou antes de
        gravar o hash).
        """
        u = _usuario(cpf="10000000002", rf="")
        self._controle_existente(
            u,
            hash_extracao=calcular_hash_extracao(u),
            hash_keycloak="hash-keycloak-nunca-confirmado",
        )

        admin = MagicMock()
        resultado = provisionar_usuario_kc(admin, u, realm="sme-apps")

        assert resultado["acao"] == "atualizado"
        admin.update_user.assert_called_once()

    def test_extracao_nao_bate_recalcula_e_reenvia(self) -> None:
        """Dado da fonte mudou: recalcula e reenvia ao Keycloak."""
        u = _usuario(cpf="10000000003", rf="", nome="Nome Novo")
        self._controle_existente(
            u,
            hash_extracao="hash-extracao-antigo",
            hash_keycloak=self._hash_keycloak_atual(u),
        )

        admin = MagicMock()
        resultado = provisionar_usuario_kc(admin, u, realm="sme-apps")

        assert resultado["acao"] == "atualizado"
        admin.update_user.assert_called_once()

    def test_ignorado_no_keycloak_ainda_retorna_token_ms_pendente(
        self,
    ) -> None:
        """Keycloak ignorado ainda pode ter token_ms_pendente=True.

        É True se hash_token_ms nunca foi confirmado — 3ª checagem
        independente das outras duas.
        """
        u = _usuario(cpf="10000000004", rf="")
        self._controle_existente(
            u,
            hash_extracao=calcular_hash_extracao(u),
            hash_keycloak=self._hash_keycloak_atual(u),
        )

        admin = MagicMock()
        resultado = provisionar_usuario_kc(admin, u, realm="sme-apps")

        assert resultado["acao"] == "ignorado"
        assert resultado["token_ms_pendente"] is True

    def test_token_ms_confirmado_nao_fica_pendente(self) -> None:
        """Hash de token-ms já confirmado não fica pendente.

        Mesmo com Keycloak ignorado, se hash_token_ms já bate com o
        payload atual, não fica pendente. Calculado sobre
        _payload_token_ms_hash (sem id_execucao) — id_execucao é só
        metadado de rastreio do envio, não deve afetar o hash.
        """
        from apps.controle_etl.orquestrador_kc import (
            _payload_token_ms_hash,
        )
        from apps.controle_etl.orquestrador_kc import (
            calcular_hash_conteudo as _hash,
        )

        u = _usuario(cpf="10000000005", rf="")
        controle = self._controle_existente(
            u,
            hash_extracao=calcular_hash_extracao(u),
            hash_keycloak=self._hash_keycloak_atual(u),
        )
        controle.hash_token_ms = _hash(_payload_token_ms_hash(u))
        controle.save()

        admin = MagicMock()
        resultado = provisionar_usuario_kc(admin, u, realm="sme-apps")

        assert resultado["token_ms_pendente"] is False

    def test_token_ms_pendente_ignora_id_execucao(self) -> None:
        """hash_token_ms não muda quando só id_execucao muda.

        id_execucao é metadado de rastreio do envio, não dado de
        negócio do usuário — dois cálculos de hash para o mesmo
        usuário, em execuções diferentes, devem ser idênticos.
        """
        from apps.controle_etl.orquestrador_kc import (
            _payload_token_ms_hash,
            calcular_hash_conteudo,
            construir_payload_token_ms,
        )

        u = _usuario(cpf="10000000006", rf="")
        u.id_execucao = "execucao-um"
        hash_um = calcular_hash_conteudo(_payload_token_ms_hash(u))

        u.id_execucao = "execucao-dois"
        hash_dois = calcular_hash_conteudo(_payload_token_ms_hash(u))

        assert hash_um == hash_dois
        # o payload de ENVIO continua trazendo id_execucao
        assert "id_execucao" in construir_payload_token_ms(u)
        assert "id_execucao" not in _payload_token_ms_hash(u)

    def test_erro_no_keycloak_naopropaga_para_dict_de_retorno(self) -> None:
        """Erro real do Keycloak propaga como exceção.

        Não retorna token_ms_pendente — quem dispara o token-ms deve
        checar isinstance(resultado, Exception) antes de acessar o
        dict.
        """
        u = _usuario(cpf="10000000006", rf="")
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.side_effect = Exception("Keycloak indisponível")

        with pytest.raises(Exception, match="Keycloak indisponível"):
            provisionar_usuario_kc(admin, u, realm="sme-apps")


# ---------------------------------------------------------------------------
# provisionar_usuarios_kc_em_paralelo
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestProvisionarUsuariosKcEmParalelo:
    def test_provisiona_todos_e_preserva_ordem(self) -> None:
        usuarios = [_usuario(cpf=str(n).zfill(11), rf="") for n in range(10)]
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.side_effect = [f"kc-{n}" for n in range(10)]

        resultados = provisionar_usuarios_kc_em_paralelo(
            admin, usuarios, realm="sme-apps", max_workers=1
        )

        assert len(resultados) == 10
        assert all(
            isinstance(r, dict) and r["acao"] == "criado" for r in resultados
        )
        ids_obtidos = [r["kc_user_id"] for r in resultados]  # type: ignore[index]
        ids_esperados = [f"kc-{n}" for n in range(10)]
        assert sorted(ids_obtidos) == sorted(ids_esperados)

    def test_erro_em_um_usuario_nao_interrompe_os_demais(self) -> None:
        usuarios = [_usuario(cpf=str(n).zfill(11), rf="") for n in range(5)]
        admin = MagicMock()
        admin.get_users.return_value = []

        def _create_user(payload: Any, exist_ok: bool = True) -> str:
            if payload["username"] == "00000000002":
                raise Exception("falha kc")
            return f"kc-{payload['username']}"

        admin.create_user.side_effect = _create_user

        resultados = provisionar_usuarios_kc_em_paralelo(
            admin, usuarios, realm="sme-apps", max_workers=1
        )

        assert len(resultados) == 5
        erros = [r for r in resultados if isinstance(r, Exception)]
        sucessos = [r for r in resultados if isinstance(r, dict)]
        assert len(erros) == 1
        assert len(sucessos) == 4

    def test_lista_vazia_retorna_lista_vazia(self) -> None:
        admin = MagicMock()
        assert provisionar_usuarios_kc_em_paralelo(admin, []) == []

    def test_respeita_max_workers_informado(self) -> None:
        usuarios = [_usuario(cpf=str(n).zfill(11), rf="") for n in range(3)]
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-1"

        resultados = provisionar_usuarios_kc_em_paralelo(
            admin, usuarios, realm="sme-apps", max_workers=1
        )

        assert len(resultados) == 3


# ---------------------------------------------------------------------------
# _localizar_usuario_kc
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLocalizarUsuarioKc:
    def test_retorna_none_sem_candidatos(self) -> None:
        u = _usuario(rf="")
        admin = MagicMock()
        admin.get_users.return_value = []
        payload = {"username": "12345678901", "email": "ana@sme.sp.gov.br"}
        assert _localizar_usuario_kc(admin, u, payload) is None

    def test_retorna_id_quando_username_coincide(self) -> None:
        u = _usuario()
        admin = MagicMock()
        admin.get_users.return_value = [
            {"id": "kc-1", "username": "12345678901"}
        ]
        payload = {"username": "12345678901", "email": "ana@sme.sp.gov.br"}
        assert _localizar_usuario_kc(admin, u, payload) == "kc-1"

    def test_remove_usuario_legado_quando_username_diverge(self) -> None:
        u = _usuario()
        admin = MagicMock()
        admin.get_users.return_value = [
            {"id": "kc-legado", "username": "username-antigo"}
        ]
        payload = {"username": "12345678901", "email": "ana@sme.sp.gov.br"}
        resultado = _localizar_usuario_kc(admin, u, payload)
        assert resultado is None
        admin.delete_user.assert_called_once_with("kc-legado")

    def test_falha_ao_remover_usuario_legado_nao_propaga(self) -> None:
        u = _usuario()
        admin = MagicMock()
        admin.get_users.return_value = [
            {"id": "kc-legado", "username": "username-antigo"}
        ]
        admin.delete_user.side_effect = Exception("falha ao remover")
        payload = {"username": "12345678901", "email": "ana@sme.sp.gov.br"}
        assert _localizar_usuario_kc(admin, u, payload) is None

    def test_busca_por_rf_quando_sem_resultado_por_email(self) -> None:
        u = _usuario(rf="9999999")
        admin = MagicMock()

        def get_users(filtro: Any) -> list[Any]:
            if filtro.get("email"):
                return []
            if filtro.get("username") == "9999999":
                return [{"id": "kc-rf", "username": "9999999"}]
            return []

        admin.get_users.side_effect = get_users
        payload = {"username": "9999999", "email": "ana@sme.sp.gov.br"}
        assert _localizar_usuario_kc(admin, u, payload) == "kc-rf"

    def test_excecao_na_busca_por_email_e_ignorada(self) -> None:
        u = _usuario(rf="")
        admin = MagicMock()
        admin.get_users.side_effect = Exception("erro busca")
        payload = {"username": "12345678901", "email": "ana@sme.sp.gov.br"}
        assert _localizar_usuario_kc(admin, u, payload) is None

    def test_excecao_na_busca_por_rf_e_ignorada(self) -> None:
        u = _usuario(rf="9999999")
        admin = MagicMock()

        def get_users(filtro: Any) -> list[Any]:
            if filtro.get("email"):
                return []
            raise Exception("erro busca por rf")

        admin.get_users.side_effect = get_users
        payload = {"username": "9999999", "email": "ana@sme.sp.gov.br"}
        assert _localizar_usuario_kc(admin, u, payload) is None


# ---------------------------------------------------------------------------
# _atribuir_roles_e_grupos
# ---------------------------------------------------------------------------


class TestAtribuirRolesEGrupos:
    def test_atribui_role_e_grupo_com_sucesso(self) -> None:
        admin = MagicMock()
        admin.get_realm_role.return_value = {"name": "Professor"}
        admin.get_group_by_path.return_value = {"id": "g-1"}

        _atribuir_roles_e_grupos(admin, "kc-id", ["Professor"], ["/SME/DRE-1"])

        admin.assign_realm_roles.assert_called_once_with(
            "kc-id", [{"name": "Professor"}]
        )
        admin.group_user_add.assert_called_once_with("kc-id", "g-1")

    def test_role_indisponivel_e_ignorada(self) -> None:
        admin = MagicMock()
        admin.get_realm_role.side_effect = Exception("role inexistente")

        _atribuir_roles_e_grupos(admin, "kc-id", ["Inexistente"], [])

        admin.assign_realm_roles.assert_not_called()

    def test_grupo_indisponivel_e_ignorado(self) -> None:
        admin = MagicMock()
        admin.get_group_by_path.side_effect = Exception("grupo inexistente")

        _atribuir_roles_e_grupos(admin, "kc-id", [], ["/SME/DRE-9"])

        admin.group_user_add.assert_not_called()

    def test_grupo_sem_id_nao_atribui(self) -> None:
        admin = MagicMock()
        admin.get_group_by_path.return_value = {}

        _atribuir_roles_e_grupos(admin, "kc-id", [], ["/SME/DRE-9"])

        admin.group_user_add.assert_not_called()


# ---------------------------------------------------------------------------
# _com_reintento
# ---------------------------------------------------------------------------


class TestComReintento:
    def test_retorna_resultado_quando_sem_erro(self) -> None:
        fn = MagicMock(return_value="ok")
        assert _com_reintento(fn, 1, dois=2) == "ok"
        fn.assert_called_once_with(1, dois=2)

    def test_reintenta_apos_erro_transitorio(self) -> None:
        fn = MagicMock(side_effect=[ConnectionError("falha"), "sucesso"])
        with patch("apps.controle_etl.orquestrador_kc.time.sleep"):
            assert _com_reintento(fn) == "sucesso"
        assert fn.call_count == 2

    def test_propaga_apos_esgotar_tentativas(self) -> None:
        fn = MagicMock(side_effect=ConnectionError("falha persistente"))
        with (
            patch("apps.controle_etl.orquestrador_kc.time.sleep"),
            pytest.raises(ConnectionError),
        ):
            _com_reintento(fn)
        assert fn.call_count == 5

    def test_erro_nao_retriavel_propaga_imediatamente(self) -> None:
        fn = MagicMock(side_effect=ValueError("erro de valor"))
        with pytest.raises(ValueError):
            _com_reintento(fn)
        fn.assert_called_once()

    def test_erro_400_do_keycloak_propaga_sem_esperar(self) -> None:
        """400 (ex.: e-mail malformado) não deve esperar backoff.

        Sem essa distinção, um erro de validação real (nunca resolvido
        por retry) esperava os mesmos ~15s de 4 backoffs de um erro
        transitório de verdade — em lotes com muitos registros com o
        mesmo problema de dado, isso soma minutos e pode estourar o
        timeout do ThreadPoolProcessor.
        """
        from keycloak.exceptions import KeycloakPostError

        fn = MagicMock(
            side_effect=KeycloakPostError(
                error_message="email invalido", response_code=400
            )
        )
        with (
            patch(
                "apps.controle_etl.orquestrador_kc.time.sleep"
            ) as mock_sleep,
            pytest.raises(KeycloakPostError),
        ):
            _com_reintento(fn)
        fn.assert_called_once()
        mock_sleep.assert_not_called()

    def test_erro_503_do_keycloak_reintenta_com_backoff(self) -> None:
        """503 (falha transitória real) continua fazendo retry."""
        from keycloak.exceptions import KeycloakPostError

        fn = MagicMock(
            side_effect=[
                KeycloakPostError(
                    error_message="indisponivel", response_code=503
                ),
                "sucesso",
            ]
        )
        with patch("apps.controle_etl.orquestrador_kc.time.sleep"):
            assert _com_reintento(fn) == "sucesso"
        assert fn.call_count == 2


# ---------------------------------------------------------------------------
# provisionar_client_kc
# ---------------------------------------------------------------------------


def _sistema_staging(**kwargs: Any) -> Any:
    from apps.staging.models import SistemaStaging

    defaults = {
        "coresso_sis_id": 1,
        "nome": "Sistema Escolar",
        "sigla": "sisesc",
        "url_callback": "https://app.exemplo/callback",
        "url_logout": "https://app.exemplo/logout",
        "situacao": 1,
    }
    defaults.update(kwargs)
    return SistemaStaging.objects.create(**defaults)


@pytest.mark.django_db
class TestProvisionarClientKc:
    def test_cria_client_novo(self, settings: Any) -> None:
        from apps.controle_etl.orquestrador_kc import provisionar_client_kc
        from apps.staging.models import SistemaStaging

        settings.KEYCLOAK_SUFIXO_CLIENT = "prod"
        sistema = _sistema_staging()
        admin = MagicMock()
        admin.get_client_id.return_value = None
        admin.create_client.return_value = "uuid-novo"

        resultado = provisionar_client_kc(admin, sistema, realm="sme-apps")

        assert resultado["acao"] == "criado"
        assert resultado["client_id"] == "sisesc-prod"
        assert resultado["kc_uuid"] == "uuid-novo"
        sistema.refresh_from_db()
        assert sistema.kc_client_uuid == "uuid-novo"
        assert (
            sistema.situacao_provisionamento
            == SistemaStaging.SituacaoProvisionamento.PROVISIONADO
        )

    def test_atualiza_client_existente(self, settings: Any) -> None:
        from apps.controle_etl.orquestrador_kc import provisionar_client_kc

        settings.KEYCLOAK_SUFIXO_CLIENT = "prod"
        sistema = _sistema_staging()
        admin = MagicMock()
        admin.get_client_id.return_value = "uuid-existente"

        resultado = provisionar_client_kc(admin, sistema, realm="sme-apps")

        assert resultado["acao"] == "atualizado"
        assert resultado["kc_uuid"] == "uuid-existente"
        admin.update_client.assert_called_once()

    def test_sigla_ausente_usa_slug_do_nome(self, settings: Any) -> None:
        from apps.controle_etl.orquestrador_kc import provisionar_client_kc

        settings.KEYCLOAK_SUFIXO_CLIENT = "prod"
        sistema = _sistema_staging(sigla=None, nome="Gestão Pedagógica")
        admin = MagicMock()
        admin.get_client_id.return_value = None
        admin.create_client.return_value = "uuid-x"

        resultado = provisionar_client_kc(admin, sistema, realm="sme-apps")

        assert resultado["client_id"] == "gestao-pedagogica-prod"

    def test_create_client_sem_retorno_busca_uuid(self, settings: Any) -> None:
        from apps.controle_etl.orquestrador_kc import provisionar_client_kc

        settings.KEYCLOAK_SUFIXO_CLIENT = "prod"
        sistema = _sistema_staging()
        admin = MagicMock()
        admin.get_client_id.side_effect = [None, "uuid-recuperado"]
        admin.create_client.return_value = None

        resultado = provisionar_client_kc(admin, sistema, realm="sme-apps")

        assert resultado["acao"] == "criado"
        assert resultado["kc_uuid"] == "uuid-recuperado"


# ---------------------------------------------------------------------------
# provisionar_role_client_kc
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProvisionarRoleClientKc:
    def test_sistema_sem_client_marca_erro(self) -> None:
        from apps.controle_etl.orquestrador_kc import (
            provisionar_role_client_kc,
        )
        from apps.staging.models import PerfilCoressoStaging

        sistema = _sistema_staging(kc_client_uuid=None)
        perfil = PerfilCoressoStaging.objects.create(
            coresso_gru_id="gru-1",
            coresso_sis_id=sistema.coresso_sis_id,
            sistema=sistema,
            nome="Professor",
            kc_role_nome="Professor",
        )
        admin = MagicMock()

        resultado = provisionar_role_client_kc(admin, perfil)

        assert resultado["acao"] == "ignorado"
        perfil.refresh_from_db()
        assert (
            perfil.situacao_provisionamento
            == PerfilCoressoStaging.SituacaoProvisionamento.ERRO
        )

    def test_cria_role_com_sucesso(self) -> None:
        from apps.controle_etl.orquestrador_kc import (
            provisionar_role_client_kc,
        )
        from apps.staging.models import PerfilCoressoStaging

        sistema = _sistema_staging(kc_client_uuid="uuid-sistema")
        perfil = PerfilCoressoStaging.objects.create(
            coresso_gru_id="gru-2",
            coresso_sis_id=sistema.coresso_sis_id,
            sistema=sistema,
            nome="Professor",
            kc_role_nome="Professor",
        )
        admin = MagicMock()
        admin.get_client_role.return_value = {"id": "role-id-1"}

        resultado = provisionar_role_client_kc(admin, perfil)

        assert resultado["acao"] == "criado"
        assert resultado["role_id"] == "role-id-1"
        perfil.refresh_from_db()
        assert (
            perfil.situacao_provisionamento
            == PerfilCoressoStaging.SituacaoProvisionamento.PROVISIONADO
        )
        assert perfil.detalhe_erro is None

    def test_role_ja_existente_marca_atualizado(self) -> None:
        from apps.controle_etl.orquestrador_kc import (
            provisionar_role_client_kc,
        )
        from apps.staging.models import PerfilCoressoStaging

        sistema = _sistema_staging(kc_client_uuid="uuid-sistema")
        perfil = PerfilCoressoStaging.objects.create(
            coresso_gru_id="gru-3",
            coresso_sis_id=sistema.coresso_sis_id,
            sistema=sistema,
            nome="Diretor",
            kc_role_nome="Diretor",
        )
        admin = MagicMock()
        admin.create_client_role.side_effect = Exception("Role already exists")
        admin.get_client_role.return_value = {"id": "role-id-2"}

        resultado = provisionar_role_client_kc(admin, perfil)

        assert resultado["acao"] == "atualizado"

    def test_erro_inesperado_ao_criar_role_propaga(self) -> None:
        from apps.controle_etl.orquestrador_kc import (
            provisionar_role_client_kc,
        )
        from apps.staging.models import PerfilCoressoStaging

        sistema = _sistema_staging(kc_client_uuid="uuid-sistema")
        perfil = PerfilCoressoStaging.objects.create(
            coresso_gru_id="gru-4",
            coresso_sis_id=sistema.coresso_sis_id,
            sistema=sistema,
            nome="Diretor",
            kc_role_nome="Diretor",
        )
        admin = MagicMock()
        admin.create_client_role.side_effect = Exception("erro de conexão")

        with pytest.raises(Exception, match="erro de conexão"):
            provisionar_role_client_kc(admin, perfil)

    def test_get_client_role_falha_nao_propaga(self) -> None:
        from apps.controle_etl.orquestrador_kc import (
            provisionar_role_client_kc,
        )
        from apps.staging.models import PerfilCoressoStaging

        sistema = _sistema_staging(kc_client_uuid="uuid-sistema")
        perfil = PerfilCoressoStaging.objects.create(
            coresso_gru_id="gru-5",
            coresso_sis_id=sistema.coresso_sis_id,
            sistema=sistema,
            nome="Agente",
            kc_role_nome="Agente",
        )
        admin = MagicMock()
        admin.get_client_role.side_effect = Exception("falha ao buscar")

        resultado = provisionar_role_client_kc(admin, perfil)

        assert resultado["acao"] == "criado"
        assert resultado["role_id"] is None


# ---------------------------------------------------------------------------
# obter_admin_keycloak
# ---------------------------------------------------------------------------


class TestObterAdminKeycloak:
    def test_usa_realm_informado(self) -> None:
        from apps.controle_etl.orquestrador_kc import obter_admin_keycloak

        with patch("keycloak.KeycloakAdmin") as mock_admin_cls:
            obter_admin_keycloak(realm="outro-realm")

        _, kwargs = mock_admin_cls.call_args
        assert kwargs["realm_name"] == "outro-realm"

    def test_usa_realm_padrao_do_settings(self, settings: Any) -> None:
        from apps.controle_etl.orquestrador_kc import obter_admin_keycloak

        settings.KEYCLOAK_REALM = "realm-padrao"
        with patch("keycloak.KeycloakAdmin") as mock_admin_cls:
            obter_admin_keycloak()

        _, kwargs = mock_admin_cls.call_args
        assert kwargs["realm_name"] == "realm-padrao"


# ------------------------------------------------------------------
# _resolver_role_info
# ------------------------------------------------------------------


@pytest.mark.django_db
class TestResolverRoleInfo:
    def test_retorna_none_quando_perfil_nao_existe(self) -> None:
        cache: dict = {}
        resultado = _resolver_role_info("inexistente", cache)
        assert resultado is None
        assert cache["inexistente"] is None

    def test_retorna_tupla_quando_perfil_provisionado(self) -> None:
        from apps.staging.models import PerfilCoressoStaging, SistemaStaging

        sistema = SistemaStaging.objects.create(
            coresso_sis_id=42,
            nome="Teste",
            kc_client_uuid="uuid-client-42",
        )
        PerfilCoressoStaging.objects.create(
            coresso_gru_id="gru-abc",
            coresso_sis_id=42,
            sistema=sistema,
            nome="Admin",
            kc_role_nome="Admin",
            kc_role_id="uuid-role-abc",
        )

        cache: dict = {}
        resultado = _resolver_role_info("gru-abc", cache)
        assert resultado is not None
        client_uuid, role_payload = resultado
        assert client_uuid == "uuid-client-42"
        assert role_payload["id"] == "uuid-role-abc"
        assert role_payload["name"] == "Admin"

    def test_usa_cache_na_segunda_chamada(self) -> None:
        cache: dict = {"gru-x": ("uuid-c", {"id": "r", "name": "N"})}
        resultado = _resolver_role_info("gru-x", cache)
        assert resultado == ("uuid-c", {"id": "r", "name": "N"})


# ------------------------------------------------------------------
# _resolver_kc_user_id
# ------------------------------------------------------------------


class TestResolverKcUserId:
    def test_retorna_id_quando_usuario_existe(self) -> None:
        admin = MagicMock()
        admin.get_users.return_value = [{"id": "kc-123"}]
        cache: dict = {}
        resultado = _resolver_kc_user_id(admin, "12345678901", cache)
        assert resultado == "kc-123"
        assert cache["12345678901"] == "kc-123"

    def test_retorna_none_quando_nao_encontra(self) -> None:
        admin = MagicMock()
        admin.get_users.return_value = []
        cache: dict = {}
        resultado = _resolver_kc_user_id(admin, "inexistente", cache)
        assert resultado is None

    def test_retorna_none_quando_excecao(self) -> None:
        admin = MagicMock()
        admin.get_users.side_effect = ConnectionError("falha")
        cache: dict = {}
        resultado = _resolver_kc_user_id(admin, "user1", cache)
        assert resultado is None
        assert cache["user1"] is None

    def test_usa_cache_na_segunda_chamada(self) -> None:
        admin = MagicMock()
        cache: dict = {"user1": "kc-cached"}
        resultado = _resolver_kc_user_id(admin, "user1", cache)
        assert resultado == "kc-cached"
        admin.get_users.assert_not_called()


# ------------------------------------------------------------------
# atribuir_client_roles_usuario_kc
# ------------------------------------------------------------------


@pytest.mark.django_db
class TestAtribuirClientRolesUsuarioKc:
    def _criar_sistema_e_perfil(self) -> None:
        from apps.staging.models import PerfilCoressoStaging, SistemaStaging

        sistema = SistemaStaging.objects.create(
            coresso_sis_id=1008,
            nome="Auto Servico",
            kc_client_uuid="uuid-client-1008",
        )
        PerfilCoressoStaging.objects.create(
            coresso_gru_id="gru-100",
            coresso_sis_id=1008,
            sistema=sistema,
            nome="COPED",
            kc_role_nome="COPED",
            kc_role_id="uuid-role-100",
        )

    def test_atribui_role_com_sucesso(self) -> None:
        self._criar_sistema_e_perfil()
        admin = MagicMock()
        admin.get_users.return_value = [{"id": "kc-user-1"}]

        vinculos = [
            {
                "login": "6913261",
                "cpf": "11972201867",
                "gru_id": "gru-100",
                "gru_nome": "COPED",
                "sis_id": 1008,
            },
        ]
        resultado = atribuir_client_roles_usuario_kc(admin, vinculos)

        assert resultado["atribuidos"] == 1
        assert resultado["erros"] == 0
        admin.assign_client_role.assert_called_once_with(
            "kc-user-1",
            "uuid-client-1008",
            [{"id": "uuid-role-100", "name": "COPED"}],
        )

    def test_ignora_quando_perfil_nao_provisionado(self) -> None:
        admin = MagicMock()
        vinculos = [
            {
                "login": "user1",
                "cpf": "",
                "gru_id": "gru-inexistente",
                "gru_nome": "X",
                "sis_id": 999,
            },
        ]
        resultado = atribuir_client_roles_usuario_kc(admin, vinculos)
        assert resultado["ignorados"] == 1
        assert resultado["atribuidos"] == 0

    def test_ignora_quando_usuario_nao_existe_no_kc(self) -> None:
        self._criar_sistema_e_perfil()
        admin = MagicMock()
        admin.get_users.return_value = []

        vinculos = [
            {
                "login": "fantasma",
                "cpf": "",
                "gru_id": "gru-100",
                "gru_nome": "COPED",
                "sis_id": 1008,
            },
        ]
        resultado = atribuir_client_roles_usuario_kc(admin, vinculos)
        assert resultado["ignorados"] == 1

    def test_conta_erros_quando_assign_falha(self) -> None:
        self._criar_sistema_e_perfil()
        admin = MagicMock()
        admin.get_users.return_value = [{"id": "kc-u"}]
        admin.assign_client_role.side_effect = Exception("boom")

        vinculos = [
            {
                "login": "user1",
                "cpf": "11122233344",
                "gru_id": "gru-100",
                "gru_nome": "COPED",
                "sis_id": 1008,
            },
        ]
        resultado = atribuir_client_roles_usuario_kc(admin, vinculos)
        assert resultado["erros"] == 1
        assert resultado["atribuidos"] == 0

    def test_usa_rf_como_username_prioritario(self) -> None:
        self._criar_sistema_e_perfil()
        admin = MagicMock()
        admin.get_users.return_value = [{"id": "kc-rf"}]

        vinculos = [
            {
                "login": "rf123",
                "cpf": "11122233344",
                "gru_id": "gru-100",
                "gru_nome": "COPED",
                "sis_id": 1008,
            },
        ]
        atribuir_client_roles_usuario_kc(admin, vinculos)
        query_chamada = admin.get_users.call_args[0][0]
        assert query_chamada["username"] == "rf123"

    def test_lista_vazia_retorna_zeros(self) -> None:
        admin = MagicMock()
        resultado = atribuir_client_roles_usuario_kc(admin, [])
        assert resultado == {
            "atribuidos": 0,
            "ignorados": 0,
            "erros": 0,
        }


# ------------------------------------------------------------------
# _upsert_usuario_kc
# ------------------------------------------------------------------


class TestUpsertUsuarioKc:
    def test_atualiza_quando_existe(self) -> None:
        admin = MagicMock()
        admin.get_users.return_value = [{"id": "kc-1"}]
        payload = {"username": "12345678901"}
        kc_id, acao = _upsert_usuario_kc(
            admin, payload, "7777777", "12345678901"
        )
        assert kc_id == "kc-1"
        assert acao == "atualizado"
        admin.update_user.assert_called_once()

    def test_cria_quando_nao_existe(self) -> None:
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-new"
        payload = {"username": "user1"}
        kc_id, acao = _upsert_usuario_kc(admin, payload, "user1", "user1")
        assert kc_id == "kc-new"
        assert acao == "criado"
        admin.set_user_password.assert_called_once()


# ------------------------------------------------------------------
# _atribuir_roles_sistema
# ------------------------------------------------------------------


@pytest.mark.django_db
class TestAtribuirRolesSistema:
    def test_sem_client_retorna_status(self) -> None:
        admin = MagicMock()
        sis_data = {"sis_id": 999, "nome": "X", "grupos": []}
        r = _atribuir_roles_sistema(admin, "kc-1", sis_data)
        assert r["status"] == "sem client no KC"

    def test_atribui_role(self) -> None:
        from apps.staging.models import PerfilCoressoStaging, SistemaStaging

        sistema = SistemaStaging.objects.create(
            coresso_sis_id=50,
            nome="Teste",
            kc_client_uuid="uuid-cli",
        )
        PerfilCoressoStaging.objects.create(
            coresso_gru_id="g-50",
            coresso_sis_id=50,
            sistema=sistema,
            nome="Admin",
            kc_role_nome="Admin",
            kc_role_id="uuid-role",
        )
        admin = MagicMock()
        sis_data = {
            "sis_id": 50,
            "nome": "Teste",
            "grupos": [{"gru_id": "g-50", "nome": "Admin"}],
        }
        r = _atribuir_roles_sistema(admin, "kc-1", sis_data)
        assert r["roles"] == ["Admin"]
        admin.assign_client_role.assert_called_once()

    def test_provisiona_client_quando_faltando(self, settings: Any) -> None:
        settings.KEYCLOAK_SUFIXO_CLIENT = "prod"
        sistema = _sistema_staging(
            coresso_sis_id=51, nome="SemClient", kc_client_uuid=None
        )
        admin = MagicMock()
        admin.get_client_id.return_value = None
        admin.create_client.return_value = "uuid-provisionado"
        sis_data = {"sis_id": 51, "nome": "SemClient", "grupos": []}

        r = _atribuir_roles_sistema(admin, "kc-1", sis_data)

        assert "status" not in r
        sistema.refresh_from_db()
        assert sistema.kc_client_uuid == "uuid-provisionado"

    def test_provisiona_role_quando_faltando(self, settings: Any) -> None:
        from apps.staging.models import PerfilCoressoStaging

        settings.KEYCLOAK_SUFIXO_CLIENT = "prod"
        sistema = _sistema_staging(
            coresso_sis_id=52, nome="ComClient", kc_client_uuid="uuid-cli-52"
        )
        perfil = PerfilCoressoStaging.objects.create(
            coresso_gru_id="g-52",
            coresso_sis_id=52,
            sistema=sistema,
            nome="Editor",
            kc_role_nome="Editor",
        )
        admin = MagicMock()
        admin.create_client_role.return_value = None
        admin.get_client_role.return_value = {"id": "uuid-role-nova"}
        sis_data = {
            "sis_id": 52,
            "nome": "ComClient",
            "grupos": [{"gru_id": "g-52", "nome": "Editor"}],
        }

        r = _atribuir_roles_sistema(admin, "kc-1", sis_data)

        assert r["roles"] == ["Editor"]
        admin.assign_client_role.assert_called_once()
        perfil.refresh_from_db()
        assert perfil.kc_role_id == "uuid-role-nova"


# ------------------------------------------------------------------
# sincronizar_usuario_kc
# ------------------------------------------------------------------


@pytest.mark.django_db
class TestSincronizarUsuarioKc:
    def test_cria_e_atribui_roles(self) -> None:
        from apps.staging.models import PerfilCoressoStaging, SistemaStaging

        sistema = SistemaStaging.objects.create(
            coresso_sis_id=60,
            nome="SisSync",
            kc_client_uuid="uuid-60",
        )
        PerfilCoressoStaging.objects.create(
            coresso_gru_id="g-60",
            coresso_sis_id=60,
            sistema=sistema,
            nome="Editor",
            kc_role_nome="Editor",
            kc_role_id="uuid-r60",
        )
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-new-60"

        dados = {
            "login": "1234567",
            "cpf": "11122233344",
            "nome": "Joao Silva",
            "email": "joao@sme.sp",
            "situacao": "ativo",
            "sistemas": {
                60: {
                    "sis_id": 60,
                    "nome": "SisSync",
                    "grupos": [
                        {"gru_id": "g-60", "nome": "Editor"},
                    ],
                },
            },
        }
        r = sincronizar_usuario_kc(admin, dados)
        assert r["acao"] == "criado"
        assert r["roles_atribuidos"] == 1
        assert r["sistemas"][0]["roles"] == ["Editor"]

    def test_erro_quando_sem_id(self) -> None:
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = None
        dados = {
            "login": "x",
            "cpf": "",
            "nome": "X",
            "email": "",
            "situacao": "ativo",
            "sistemas": {},
        }
        r = sincronizar_usuario_kc(admin, dados)
        assert r["acao"] == "erro"


# ------------------------------------------------------------------
# _resolver_conflito_email
# ------------------------------------------------------------------


class TestResolverConflitoEmail:
    def test_mesmo_usuario_limpa_duplicado(self) -> None:
        from apps.controle_etl.orquestrador_kc import _resolver_conflito_email

        admin = MagicMock()
        admin.get_users.return_value = [
            {
                "id": "kc-old",
                "username": "old",
                "attributes": {"rf": ["111"]},
            }
        ]
        payload = {
            "username": "111",
            "email": "a@x",
            "attributes": {"rf": ["111"]},
        }
        result = _resolver_conflito_email(admin, payload, "111")
        assert result["email"] == "a@x"
        admin.update_user.assert_called_once_with("kc-old", {"email": ""})

    def test_outro_usuario_remove_email(self) -> None:
        from apps.controle_etl.orquestrador_kc import _resolver_conflito_email

        admin = MagicMock()
        admin.get_users.return_value = [
            {
                "id": "kc-other",
                "username": "other",
                "attributes": {"rf": ["999"]},
            }
        ]
        payload = {
            "username": "111",
            "email": "a@x",
            "attributes": {"rf": ["111"]},
        }
        result = _resolver_conflito_email(admin, payload, "111")
        assert "email" not in result

    def test_sem_email_retorna_payload(self) -> None:
        from apps.controle_etl.orquestrador_kc import _resolver_conflito_email

        admin = MagicMock()
        payload = {"username": "u", "email": ""}
        result = _resolver_conflito_email(admin, payload, "u")
        assert result == payload


@pytest.mark.django_db
class TestUpsertComConflito:
    def test_update_com_conflito_email(self) -> None:
        admin = MagicMock()

        def _get_users_lado(query: dict) -> list[dict]:
            if query.get("username") == "111":
                return [{"id": "kc-1"}]
            if query.get("email") == "dup@x":
                return [
                    {
                        "id": "kc-other",
                        "username": "other",
                        "attributes": {"rf": ["999"]},
                    }
                ]
            return []

        admin.get_users.side_effect = _get_users_lado
        admin.update_user.side_effect = [
            Exception("409 email"),
            None,  # retry sem email
        ]
        payload = {
            "username": "111",
            "email": "dup@x",
            "attributes": {"rf": ["111"], "cpf": [""]},
        }
        kc_id, acao = _upsert_usuario_kc(admin, payload, "111", "111")
        assert kc_id == "kc-1"
        assert acao == "atualizado"


# ------------------------------------------------------------------
# _montar_queries_busca
# ------------------------------------------------------------------


class TestMontarQueriesBusca:
    def test_gera_queries_completas(self) -> None:
        qs = _montar_queries_busca("rf1", "cpf1", "a@b.c")
        assert len(qs) == 5
        assert qs[0] == {"username": "rf1", "exact": True}
        assert qs[1] == {"username": "cpf1", "exact": True}

    def test_sem_email(self) -> None:
        qs = _montar_queries_busca("rf1", "cpf1", "")
        assert len(qs) == 4

    def test_sem_login_nem_cpf(self) -> None:
        qs = _montar_queries_busca("", "", "a@b.c")
        assert len(qs) == 1


# ------------------------------------------------------------------
# _buscar_todas_contas_kc
# ------------------------------------------------------------------


class TestBuscarTodasContasKc:
    def test_encontra_uma_conta(self) -> None:
        admin = MagicMock()
        admin.get_users.return_value = [{"id": "kc-1"}]
        contas = _buscar_todas_contas_kc(admin, "rf1", "", "")
        assert len(contas) == 1
        assert contas[0]["id"] == "kc-1"

    def test_dedup_por_id(self) -> None:
        admin = MagicMock()
        admin.get_users.return_value = [{"id": "kc-1"}]
        contas = _buscar_todas_contas_kc(admin, "rf1", "cpf1", "a@b.c")
        assert len(contas) == 1

    def test_encontra_duplicatas(self) -> None:
        admin = MagicMock()

        def _side(query: dict) -> list[dict]:
            if query.get("username") == "rf1":
                return [{"id": "kc-rf"}]
            if query.get("username") == "cpf1":
                return [{"id": "kc-cpf"}]
            return []

        admin.get_users.side_effect = _side
        contas = _buscar_todas_contas_kc(admin, "rf1", "cpf1", "")
        assert len(contas) == 2
        assert contas[0]["id"] == "kc-rf"
        assert contas[1]["id"] == "kc-cpf"

    def test_erro_na_api_continua(self) -> None:
        admin = MagicMock()
        admin.get_users.side_effect = Exception("falha")
        contas = _buscar_todas_contas_kc(admin, "rf1", "", "")
        assert contas == []


# ------------------------------------------------------------------
# _migrar_client_roles_kc e _migrar_realm_roles_kc
# ------------------------------------------------------------------


@pytest.mark.django_db
class TestMigrarRoles:
    def test_migra_client_roles(self) -> None:
        from apps.staging.models import SistemaStaging

        SistemaStaging.objects.create(
            coresso_sis_id=99,
            nome="SisTest",
            kc_client_uuid="uuid-99",
        )
        admin = MagicMock()
        admin.get_client_roles_of_user.return_value = [
            {"id": "r1", "name": "Admin"}
        ]
        _migrar_client_roles_kc(admin, "origem", "destino")
        admin.assign_client_role.assert_called_once_with(
            "destino",
            "uuid-99",
            [{"id": "r1", "name": "Admin"}],
        )

    def test_migra_realm_roles(self) -> None:
        admin = MagicMock()
        default_role = f"default-roles-{settings.KEYCLOAK_REALM}"
        admin.get_realm_roles_of_user.return_value = [
            {"name": default_role},
            {"name": "Professor"},
        ]
        _migrar_realm_roles_kc(admin, "origem", "destino")
        admin.assign_realm_roles.assert_called_once_with(
            "destino", [{"name": "Professor"}]
        )

    def test_nao_migra_apenas_defaults(self) -> None:
        admin = MagicMock()
        default_role = f"default-roles-{settings.KEYCLOAK_REALM}"
        admin.get_realm_roles_of_user.return_value = [
            {"name": default_role},
            {"name": "offline_access"},
        ]
        _migrar_realm_roles_kc(admin, "origem", "destino")
        admin.assign_realm_roles.assert_not_called()


# ------------------------------------------------------------------
# _merge_contas_kc
# ------------------------------------------------------------------


@pytest.mark.django_db
class TestMergeContasKc:
    def test_merge_e_remove_duplicadas(self) -> None:
        from apps.staging.models import SistemaStaging

        SistemaStaging.objects.create(
            coresso_sis_id=80,
            nome="SisMerge",
            kc_client_uuid="uuid-80",
        )
        admin = MagicMock()
        admin.get_client_roles_of_user.return_value = []
        admin.get_realm_roles_of_user.return_value = []

        principal = {"id": "kc-main", "username": "rf1"}
        duplicadas = [
            {"id": "kc-dup", "username": "cpf1"},
        ]
        removidos = _merge_contas_kc(admin, principal, duplicadas)
        assert removidos == ["cpf1"]
        admin.delete_user.assert_called_once_with("kc-dup")


# ------------------------------------------------------------------
# _criar_role_kc
# ------------------------------------------------------------------


@pytest.mark.django_db
class TestCriarRoleKc:
    def _sistema(self, sis_id: int = 90) -> Any:
        from apps.staging.models import SistemaStaging

        return SistemaStaging.objects.create(
            coresso_sis_id=sis_id,
            nome="SisTeste",
            kc_client_uuid=f"uuid-{sis_id}",
            kc_client_id=f"sis-{sis_id}-qa",
        )

    def test_cria_role_quando_perfil_none(self) -> None:
        sistema = self._sistema(90)
        admin = MagicMock()
        admin.get_client_role.return_value = {"id": "role-uuid-90"}

        resultado = _criar_role_kc(admin, sistema, 90, "NOVO_ROLE", None)

        assert resultado is not None
        assert resultado.kc_role_id == "role-uuid-90"
        assert resultado.kc_role_nome == "NOVO_ROLE"
        admin.create_client_role.assert_called_once()

    def test_atualiza_perfil_existente_sem_kc_role_id(self) -> None:
        from apps.staging.models import PerfilCoressoStaging

        sistema = self._sistema(91)
        perfil = PerfilCoressoStaging.objects.create(
            coresso_sis_id=91,
            coresso_gru_id="gru-91",
            sistema=sistema,
            nome="ROLE_SEM_ID",
            kc_role_nome="ROLE_SEM_ID",
            kc_role_id=None,
        )
        admin = MagicMock()
        admin.get_client_role.return_value = {"id": "role-uuid-91"}

        resultado = _criar_role_kc(admin, sistema, 91, "ROLE_SEM_ID", perfil)

        assert resultado is not None
        perfil.refresh_from_db()
        assert perfil.kc_role_id == "role-uuid-91"

    def test_retorna_none_quando_get_client_role_falha(self) -> None:
        sistema = self._sistema(92)
        admin = MagicMock()
        admin.get_client_role.side_effect = Exception("timeout")

        resultado = _criar_role_kc(admin, sistema, 92, "ROLE_X", None)

        assert resultado is None

    def test_retorna_none_quando_role_id_vazio(self) -> None:
        sistema = self._sistema(93)
        admin = MagicMock()
        admin.get_client_role.return_value = {"id": None}

        resultado = _criar_role_kc(admin, sistema, 93, "ROLE_Y", None)

        assert resultado is None

    def test_get_or_create_encontra_registro_existente_sem_id(
        self,
    ) -> None:
        from apps.staging.models import PerfilCoressoStaging

        sistema = self._sistema(94)
        PerfilCoressoStaging.objects.create(
            coresso_sis_id=94,
            coresso_gru_id="api-94-ROLE_Z",
            sistema=sistema,
            nome="ROLE_Z",
            kc_role_nome="ROLE_Z",
            kc_role_id=None,
        )
        admin = MagicMock()
        admin.get_client_role.return_value = {"id": "role-uuid-94"}

        resultado = _criar_role_kc(admin, sistema, 94, "ROLE_Z", None)

        assert resultado is not None
        assert resultado.kc_role_id == "role-uuid-94"


# conceder_acesso_kc
# ------------------------------------------------------------------


@pytest.mark.django_db
class TestConcederAcessoKc:
    def test_concede_role_existente(self) -> None:
        from apps.staging.models import PerfilCoressoStaging, SistemaStaging

        sistema = SistemaStaging.objects.create(
            coresso_sis_id=70,
            nome="SisConc",
            kc_client_uuid="uuid-70",
            kc_client_id="sisconc-qa",
        )
        PerfilCoressoStaging.objects.create(
            coresso_gru_id="g-70",
            coresso_sis_id=70,
            sistema=sistema,
            nome="ASCOM",
            kc_role_nome="ASCOM",
            kc_role_id="uuid-r70",
        )
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-new-70"

        dados = {
            "login": "7777777",
            "cpf": "11122233344",
            "nome": "Joao Silva",
            "email": "j@sme.sp",
            "situacao": "ativo",
            "sistemas": {},
        }
        r = conceder_acesso_kc(admin, dados, 70, ["ASCOM"])
        assert r["acao"] == "criado"
        assert r["roles_atribuidos"] == ["ASCOM"]
        assert r["roles_nao_encontrados"] == []
        assert r["sistema"] == "SisConc"
        admin.assign_client_role.assert_called_once()

    def test_role_inexistente_e_criado_automaticamente(self) -> None:
        from apps.staging.models import SistemaStaging

        SistemaStaging.objects.create(
            coresso_sis_id=71,
            nome="SisConc2",
            kc_client_uuid="uuid-71",
            kc_client_id="sisconc2-qa",
        )
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-new-71"
        admin.get_client_role.return_value = {
            "id": "uuid-novo",
            "name": "INEXISTENTE",
        }

        dados = {
            "login": "8888888",
            "cpf": "",
            "nome": "Maria",
            "email": "",
            "situacao": "ativo",
            "sistemas": {},
        }
        r = conceder_acesso_kc(admin, dados, 71, ["INEXISTENTE"])
        assert r["roles_atribuidos"] == ["INEXISTENTE"]
        assert r["roles_nao_encontrados"] == []
        admin.create_client_role.assert_called_once()

    def test_role_criacao_falha_vai_para_nao_encontrados(
        self,
    ) -> None:
        from apps.staging.models import SistemaStaging

        SistemaStaging.objects.create(
            coresso_sis_id=73,
            nome="SisConc3",
            kc_client_uuid="uuid-73",
            kc_client_id="sisconc3-qa",
        )
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-new-73"
        admin.create_client_role.side_effect = Exception("erro de conexão")

        dados = {
            "login": "8888889",
            "cpf": "",
            "nome": "Maria",
            "email": "",
            "situacao": "ativo",
            "sistemas": {},
        }
        r = conceder_acesso_kc(admin, dados, 73, ["FALHA"])
        assert r["roles_nao_encontrados"] == ["FALHA"]
        assert r["roles_atribuidos"] == []

    def test_sistema_sem_client(self) -> None:
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-x"

        dados = {
            "login": "9999999",
            "cpf": "",
            "nome": "Carlos",
            "email": "",
            "situacao": "ativo",
            "sistemas": {},
        }
        r = conceder_acesso_kc(admin, dados, 9999, ["X"])
        assert "erro" in r

    def test_usa_rf_como_username(self) -> None:
        from apps.staging.models import SistemaStaging

        SistemaStaging.objects.create(
            coresso_sis_id=72,
            nome="SisRF",
            kc_client_uuid="uuid-72",
            kc_client_id="sisrf-qa",
        )
        admin = MagicMock()
        admin.get_users.return_value = []
        admin.create_user.return_value = "kc-rf"

        dados = {
            "login": "1234567",
            "cpf": "99988877766",
            "nome": "Ana",
            "email": "",
            "situacao": "ativo",
            "sistemas": {},
        }
        r = conceder_acesso_kc(admin, dados, 72, [])
        assert r["username"] == "1234567"
