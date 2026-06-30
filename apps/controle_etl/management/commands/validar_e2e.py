"""Comando Django para validar o pipeline ponta a ponta em ambiente real."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.controle_etl.models import ExecucaoETL
from apps.controle_etl.orquestrador_kc import (
    _resolver_username,
    obter_admin_keycloak,
)
from apps.controle_etl.tasks import (
    task_identidade_extrair_coresso,
    task_identidade_extrair_eol_alunos,
    task_identidade_extrair_se1426,
    task_identidade_resolver_identidade,
    task_provisionar_identidade_keycloak,
)
from apps.staging.models import (
    PerfilCoressoStaging,
    SistemaStaging,
    UsuarioAlunoStaging,
    UsuarioServidorStaging,
    UsuarioTerceiroStaging,
)

_MD_TABLE_SEP = "|---|---|---|---|"

_FONTES_MODELO = (
    ("se1426", UsuarioServidorStaging),
    ("coresso", UsuarioTerceiroStaging),
    ("eol_alunos", UsuarioAlunoStaging),
)


def _identificador_exibicao(usuario: Any) -> str:
    """Retorna o identificador mais legível do usuário."""
    return (
        getattr(usuario, "rf", None)
        or usuario.cpf
        or getattr(usuario, "matricula", None)
        or "—"
    )


def _url_usuario_kc(kc_user_id: str | None, realm: str) -> str:
    """Monta a URL do admin console do Keycloak."""
    if not kc_user_id:
        return "—"
    base = settings.KEYCLOAK_URL_SERVIDOR.rstrip("/")
    return (
        f"{base}/admin/master/console/"
        + f"#/{realm}/users/{kc_user_id}/settings"
    )


def _info_usuario_kc(u: dict) -> dict[str, Any]:
    """Extrai info relevante de um usuário do Keycloak."""
    return {
        "encontrado": True,
        "kc_user_id": str(u["id"]),
        "nome": (
            f"{u.get('firstName', '')}" + f" {u.get('lastName', '')}"
        ).strip(),
        "email": u.get("email", ""),
    }


class Command(BaseCommand):
    """Executa extração → resolução → Keycloak com volume reduzido."""

    help = (
        "Valida o pipeline ponta a ponta. Use --sis-id e/ou"
        + " --gru-id para filtrar por sistema/grupo."
        + " Use --forcar-atualizacao para forçar update no KC."
    )

    def add_arguments(self, parser: Any) -> None:
        """Define os argumentos do comando."""
        parser.add_argument(
            "--lote-maximo",
            type=int,
            default=15,
            help="Teto de registros por fonte. Padrão: 15.",
        )
        parser.add_argument(
            "--realm",
            type=str,
            default=settings.KEYCLOAK_REALM,
            help="Realm Keycloak.",
        )
        parser.add_argument(
            "--saida",
            type=str,
            default="",
            help=(
                "Caminho do relatório. Padrão:"
                + " validacao_e2e/{data_hora}.md"
            ),
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=None,
            help="Sobrepõe ETL_CHUNK_SIZE nesta execução.",
        )
        parser.add_argument(
            "--sis-id",
            type=int,
            default=None,
            help=(
                "Filtra por sistema CoreSSO (sis_id)."
                + " Ex: 1008 (Auto Serviço). Omitir = todos."
            ),
        )
        parser.add_argument(
            "--gru-id",
            type=str,
            default=None,
            help=(
                "Filtra por grupo CoreSSO (gru_id UUID)." + " Omitir = todos."
            ),
        )
        parser.add_argument(
            "--forcar-atualizacao",
            action="store_true",
            default=False,
            help=(
                "Força update dos usuários no Keycloak"
                + " mesmo sem mudança de dados."
            ),
        )
        parser.add_argument(
            "--fonte",
            type=str,
            default="todos",
            choices=[
                "todos",
                "se1426",
                "coresso",
                "eol_alunos",
            ],
            help=(
                "Fonte a extrair: todos (padrão),"
                + " se1426, coresso ou eol_alunos."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Executa o pipeline reduzido e grava o relatório."""
        lote_maximo = options["lote_maximo"]
        realm = options["realm"]
        sis_id = options["sis_id"]
        gru_id = options["gru_id"]
        forcar = options["forcar_atualizacao"]
        fonte = options["fonte"]

        if options["saida"]:
            caminho_saida = Path(options["saida"])
        else:
            agora = timezone.now().strftime("%Y%m%d_%H%M%S")
            pasta = Path("validacao_e2e")
            pasta.mkdir(exist_ok=True)
            caminho_saida = pasta / f"{agora}.md"

        settings.ETL_LOTE_MAXIMO = lote_maximo
        if options["chunk_size"]:
            settings.ETL_CHUNK_SIZE = options["chunk_size"]

        execucao = ExecucaoETL.objects.create(
            fonte="todos",
            realm_destino=realm,
            tipo_disparo=ExecucaoETL.TipoDisparo.MANUAL,
        )
        execucao.marcar_executando()
        id_exec = str(execucao.id_execucao)

        filtro_desc = f" | fonte={fonte}"
        if sis_id:
            filtro_desc += f" | sis_id={sis_id}"
        if gru_id:
            filtro_desc += f" | gru_id={gru_id}"
        if forcar:
            filtro_desc += " | forcar_atualizacao=True"
        self.stdout.write(
            f"ExecucaoETL criada: {id_exec}"
            + f" | lote_maximo={lote_maximo}"
            + f" | realm={realm}{filtro_desc}"
        )

        resultado_sistemas = self._extrair_e_provisionar_sistemas(realm)
        resultado_perfis = self._extrair_e_provisionar_perfis(realm)

        resultado_vinculos, usuarios_sistema = self._atribuir_vinculos(
            realm, sis_id=sis_id, gru_id=gru_id
        )

        validacoes_kc: dict = {}
        if sis_id and usuarios_sistema:
            self._criar_usuarios_ausentes_kc(realm, usuarios_sistema)
            self.stdout.write(
                "\n--- Validação direta no Keycloak"
                + f" ({len(usuarios_sistema)} logins) ---"
            )
            validacoes_kc = self._validar_logins_keycloak(
                realm, set(usuarios_sistema.keys())
            )
        else:
            usuarios_sistema = {}
            self._extrair_e_provisionar_usuarios(id_exec, fonte)
            execucao.refresh_from_db()

            self.stdout.write("\n--- Validação no Keycloak ---")
            validacoes_kc = self._validar_amostra_keycloak(
                id_exec, realm, None
            )

        execucao.refresh_from_db()
        situacao_final = (
            ExecucaoETL.Situacao.FALHA
            if execucao.total_erros
            else ExecucaoETL.Situacao.SUCESSO
        )
        execucao.marcar_finalizada(situacao_final)
        self.stdout.write(
            self.style.SUCCESS(
                "\nPipeline concluído" + f" | situacao={execucao.situacao}"
            )
        )

        self._gravar_relatorio(
            caminho_saida,
            execucao,
            id_exec,
            validacoes_kc,
            resultado_sistemas,
            resultado_perfis,
            resultado_vinculos,
            usuarios_sistema,
            sis_id,
        )
        self.stdout.write(
            self.style.SUCCESS(f"\nRelatório gerado em {caminho_saida}")
        )

    def _extrair_e_provisionar_usuarios(
        self, id_exec: str, fonte: str
    ) -> None:
        """Extrai usuários das fontes e provisiona no KC."""
        tarefas_por_fonte = {
            "se1426": (
                "SE1426",
                task_identidade_extrair_se1426,
            ),
            "coresso": (
                "CoreSSO",
                task_identidade_extrair_coresso,
            ),
            "eol_alunos": (
                "EOL_DB (alunos)",
                task_identidade_extrair_eol_alunos,
            ),
        }
        if fonte == "todos":
            tarefas = list(tarefas_por_fonte.values())
        else:
            tarefas = [tarefas_por_fonte[fonte]]

        resultados_extracao = []
        for nome, task in tarefas:
            self.stdout.write(f"\n--- Extração {nome} ---")
            t0 = time.monotonic()
            resultado = task(id_execucao=id_exec)
            self.stdout.write(
                f"{resultado}" + f" ({time.monotonic() - t0:.1f}s)"
            )
            resultados_extracao.append(resultado)

        self.stdout.write("\n--- Resolução / Dedup ---")
        resultado_resolucao = task_identidade_resolver_identidade(
            resultados_extracao, id_execucao=id_exec
        )
        self.stdout.write(str(resultado_resolucao))

        self.stdout.write("\n--- Provisionamento Keycloak (usuários) ---")
        task_provisionar_identidade_keycloak(
            resultado_resolucao, id_execucao=id_exec
        )

    def _provisionar_usuarios_do_sistema(
        self,
        id_exec: str,
        realm: str,
        logins_sistema: set[str],
        forcar: bool,
    ) -> None:
        """Busca e provisiona no KC apenas os usuários do sistema."""
        from apps.controle_etl.orquestrador_kc import provisionar_usuario_kc

        self.stdout.write(
            "\n--- Provisionamento de usuários"
            + f" do sistema ({len(logins_sistema)}"
            + " logins) ---"
        )
        admin = obter_admin_keycloak(realm=realm)
        criados = atualizados = ignorados = erros_u = 0

        for _fonte, modelo in _FONTES_MODELO:
            for usuario in modelo.objects.filter(
                id_execucao=id_exec
            ).iterator():
                if not (_identificadores_usuario(usuario) & logins_sistema):
                    continue
                try:
                    r = provisionar_usuario_kc(
                        admin,
                        usuario,
                        realm=realm,
                        forcar_atualizacao=forcar,
                    )
                    if r["acao"] == "criado":
                        criados += 1
                    elif r["acao"] == "atualizado":
                        atualizados += 1
                    else:
                        ignorados += 1
                except Exception:
                    erros_u += 1

        self.stdout.write(
            f"Usuários sistema: {criados} criados,"
            + f" {atualizados} atualizados,"
            + f" {ignorados} ignorados,"
            + f" {erros_u} erros"
        )

    def _extrair_e_provisionar_sistemas(self, realm: str) -> dict[str, int]:
        """Extrai sistemas do CoreSSO e provisiona como clients."""
        from apps.controle_etl.orquestrador_kc import provisionar_client_kc
        from apps.extracao.tasks import extrair_sistemas_coresso

        self.stdout.write("\n--- Extração de Sistemas ---")
        t0 = time.monotonic()
        total_sistemas = extrair_sistemas_coresso()
        self.stdout.write(
            f"{total_sistemas} sistemas" + f" ({time.monotonic() - t0:.1f}s)"
        )

        self.stdout.write("\n--- Provisionamento de Clients ---")
        admin = obter_admin_keycloak(realm=realm)
        criados = atualizados = erros_cli = 0
        for sistema in SistemaStaging.objects.filter(situacao=1):
            try:
                r = provisionar_client_kc(admin, sistema, realm=realm)
                if r["acao"] == "criado":
                    criados += 1
                else:
                    atualizados += 1
            except Exception as exc:
                erros_cli += 1
                self.stdout.write(
                    self.style.ERROR(f"  Client {sistema.nome}: {exc}")
                )

        self.stdout.write(
            f"Clients: {criados} criados,"
            + f" {atualizados} atualizados,"
            + f" {erros_cli} erros"
        )
        return {
            "total_sistemas": total_sistemas,
            "clients_criados": criados,
            "clients_atualizados": atualizados,
            "clients_erros": erros_cli,
        }

    def _extrair_e_provisionar_perfis(self, realm: str) -> dict[str, int]:
        """Extrai perfis do CoreSSO e provisiona como roles."""
        from apps.controle_etl.orquestrador_kc import (
            provisionar_role_client_kc,
        )
        from apps.extracao.tasks import extrair_perfis_coresso

        self.stdout.write("\n--- Extração de Perfis ---")
        t0 = time.monotonic()
        total_perfis = extrair_perfis_coresso()
        self.stdout.write(
            f"{total_perfis} perfis" + f" ({time.monotonic() - t0:.1f}s)"
        )

        self.stdout.write("\n--- Provisionamento de Client Roles ---")
        admin = obter_admin_keycloak(realm=realm)
        criados = atualizados = ignorados = erros_r = 0
        qs = PerfilCoressoStaging.objects.select_related("sistema").all()
        for perfil in qs.iterator():
            try:
                r = provisionar_role_client_kc(admin, perfil)
                if r["acao"] == "criado":
                    criados += 1
                elif r["acao"] == "atualizado":
                    atualizados += 1
                else:
                    ignorados += 1
            except Exception as exc:
                erros_r += 1
                self.stdout.write(
                    self.style.ERROR(f"  Role {perfil.nome}: {exc}")
                )

        self.stdout.write(
            f"Roles: {criados} criados,"
            + f" {atualizados} atualizados,"
            + f" {ignorados} ignorados,"
            + f" {erros_r} erros"
        )
        return {
            "total_perfis": total_perfis,
            "roles_criados": criados,
            "roles_atualizados": atualizados,
            "roles_ignorados": ignorados,
            "roles_erros": erros_r,
        }

    def _atribuir_vinculos(
        self,
        realm: str,
        *,
        sis_id: int | None = None,
        gru_id: str | None = None,
    ) -> tuple[dict[str, int], dict[str, dict[str, str]]]:
        """Extrai e atribui vínculos.

        Returns:
            Tupla (resultado, usuarios_por_login) onde
            usuarios_por_login mapeia login → {nome, email,
            cpf, grupos}.
        """
        from apps.controle_etl.orquestrador_kc import (
            atribuir_client_roles_usuario_kc,
        )
        from apps.extracao.tasks import extrair_vinculos_usuario_grupo_coresso

        self.stdout.write("\n--- Vínculos (client role assignments) ---")
        t0 = time.monotonic()
        usuarios_por_login: dict[str, dict[str, str]] = {}
        try:
            admin = obter_admin_keycloak(realm=realm)
            vinculos_raw = list(
                extrair_vinculos_usuario_grupo_coresso(
                    sis_id=sis_id, gru_id=gru_id
                )
            )
            for v in vinculos_raw:
                login = (v.get("login") or "").strip()
                if not login:
                    continue
                if login not in usuarios_por_login:
                    usuarios_por_login[login] = {
                        "nome": (v.get("nome") or "").strip(),
                        "email": (v.get("email") or "").strip(),
                        "cpf": (v.get("cpf") or "").strip(),
                        "grupos": v.get("gru_nome", ""),
                    }
                else:
                    existente = usuarios_por_login[login]
                    existente["grupos"] += f", {v.get('gru_nome', '')}"
            resultado = atribuir_client_roles_usuario_kc(
                admin, iter(vinculos_raw)
            )
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"  Vínculos: {exc}"))
            resultado = {
                "atribuidos": 0,
                "ignorados": 0,
                "erros": 1,
            }

        self.stdout.write(
            f"Vínculos: {resultado['atribuidos']}"
            + " atribuídos,"
            + f" {resultado['ignorados']} ignorados,"
            + f" {resultado['erros']} erros"
            + f" | {len(usuarios_por_login)}"
            + " usuários do sistema"
            + f" ({time.monotonic() - t0:.1f}s)"
        )
        return resultado, usuarios_por_login

    def _criar_usuarios_ausentes_kc(
        self,
        realm: str,
        usuarios_sistema: dict[str, dict[str, str]],
    ) -> None:
        """Cria no Keycloak os usuários do sistema que não existem."""
        admin = obter_admin_keycloak(realm=realm)
        criados = existentes = erros_c = 0

        self.stdout.write(
            "\n--- Provisionamento de usuários"
            + f" do sistema ({len(usuarios_sistema)}) ---"
        )

        for login, dados in usuarios_sistema.items():
            if self._buscar_usuario_kc(admin, login):
                existentes += 1
                continue
            nome = (dados.get("nome") or "").strip()
            partes = nome.split()
            payload = {
                "username": login,
                "email": dados.get("email") or "",
                "firstName": partes[0] if partes else "",
                "lastName": (" ".join(partes[1:]) if len(partes) > 1 else ""),
                "enabled": True,
                "emailVerified": False,
                "attributes": {
                    "fonte": ["coresso"],
                    "rf": [login],
                },
            }
            try:
                from apps.controle_etl.orquestrador_kc import _com_reintento

                kc_id = _com_reintento(
                    admin.create_user,
                    payload,
                    exist_ok=True,
                )
                if kc_id:
                    _com_reintento(
                        admin.set_user_password,
                        kc_id,
                        login,
                        temporary=True,
                    )
                criados += 1
            except Exception as exc:
                erros_c += 1
                self.stdout.write(self.style.ERROR(f"  {login}: {exc}"))

        self.stdout.write(
            f"Usuários: {criados} criados,"
            + f" {existentes} já existiam,"
            + f" {erros_c} erros"
        )

    def _validar_logins_keycloak(
        self,
        realm: str,
        logins: set[str],
    ) -> dict[str, dict[str, Any]]:
        """Busca logins diretamente no Keycloak.

        Tenta por username exato, depois por atributo
        ``rf`` para cobrir o caso em que o username no
        KC é o CPF mas o vínculo só tem o RF.
        """
        admin = obter_admin_keycloak(realm=realm)
        ja_encontrados: set[str] = set()
        resultado: dict[str, dict[str, Any]] = {}
        confirmados = 0

        for login in sorted(logins):
            if login in ja_encontrados:
                continue
            u = self._buscar_usuario_kc(admin, login)
            if u:
                info = _info_usuario_kc(u)
                resultado[login] = info
                ja_encontrados.add(login)
                confirmados += 1
            else:
                resultado[login] = {
                    "encontrado": False,
                    "kc_user_id": None,
                    "nome": "",
                    "email": "",
                }

        self.stdout.write(
            f"Keycloak: {confirmados}/{len(resultado)}" + " confirmados"
        )
        return resultado

    def _buscar_usuario_kc(
        self, admin: Any, login: str
    ) -> dict[str, Any] | None:
        """Busca usuário no KC por username ou atributo rf."""
        for query in [
            {"username": login, "exact": True},
            {"q": f"rf:{login}"},
            {"q": f"cpf:{login}"},
        ]:
            users = admin.get_users(query=query)
            if users:
                result: dict[str, Any] = users[0]
                return result
        return None

    def _validar_amostra_keycloak(
        self,
        id_exec: str,
        realm: str,
        logins_sistema: set[str] | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Confirma na API do Keycloak quais usuários existem.

        Se ``logins_sistema`` é informado, valida apenas
        os usuários cujo username está no conjunto.
        """
        admin = obter_admin_keycloak(realm=realm)
        resultado: dict[int, dict[str, Any]] = {}

        for fonte, modelo in _FONTES_MODELO:
            usuarios = list(modelo.objects.filter(id_execucao=id_exec))
            confirmados = 0
            total_validados = 0
            for usuario in usuarios:
                username = _resolver_username(usuario)
                if logins_sistema and not (
                    _identificadores_usuario(usuario) & logins_sistema
                ):
                    continue
                total_validados += 1
                usuarios_kc = admin.get_users(
                    query={
                        "username": username,
                        "exact": True,
                    }
                )
                encontrado = bool(usuarios_kc)
                kc_user_id = str(usuarios_kc[0]["id"]) if usuarios_kc else None
                uid = usuario.id  # type: ignore[attr-defined]
                resultado[uid] = {
                    "encontrado": encontrado,
                    "kc_user_id": kc_user_id,
                }
                confirmados += int(encontrado)

            if total_validados:
                self.stdout.write(
                    f"{fonte}:"
                    + f" {confirmados}/{total_validados}"
                    + " confirmados no Keycloak"
                )

        return resultado

    def _gravar_relatorio(
        self,
        caminho: Path,
        execucao: ExecucaoETL,
        id_exec: str,
        validacoes_kc: dict,
        resultado_sistemas: dict[str, int],
        resultado_perfis: dict[str, int],
        resultado_vinculos: dict[str, int],
        usuarios_sistema: dict[str, dict] | None = None,
        sis_id: int | None = None,
    ) -> None:
        """Monta o relatório Markdown de validação manual."""
        realm = execucao.realm_destino
        linhas = [
            ("# Relatório de validação E2E" + " — SME-Identidade-ETL"),
            "",
            f"- **Execução:** `{id_exec}`",
            ("- **Gerado em:**" + f" {timezone.now():%Y-%m-%d %H:%M:%S}"),
            f"- **Realm Keycloak:** {realm}",
            f"- **Situação final:** {execucao.situacao}",
        ]
        if sis_id:
            nome_sis = ""
            s = SistemaStaging.objects.filter(coresso_sis_id=sis_id).first()
            if s:
                nome_sis = f" ({s.nome})"
            linhas.append(
                f"- **Filtro sistema:** sis_id={sis_id}" + f"{nome_sis}"
            )

        sis = resultado_sistemas
        prf = resultado_perfis
        vnc = resultado_vinculos
        linhas += [
            "",
            "## Sistemas e Clients",
            "",
            f"- Sistemas extraídos: {sis['total_sistemas']}",
            f"- Clients criados: {sis['clients_criados']}",
            f"- Clients atualizados: {sis['clients_atualizados']}",
            f"- Clients erros: {sis['clients_erros']}",
            "",
            "## Perfis e Client Roles",
            "",
            f"- Perfis extraídos: {prf['total_perfis']}",
            f"- Roles criados: {prf['roles_criados']}",
            f"- Roles atualizados: {prf['roles_atualizados']}",
            f"- Roles ignorados: {prf['roles_ignorados']}",
            f"- Roles erros: {prf['roles_erros']}",
            "",
            "## Vínculos (client role assignments)",
            "",
            f"- Atribuídos: {vnc['atribuidos']}",
            f"- Ignorados: {vnc['ignorados']}",
            f"- Erros: {vnc['erros']}",
            "",
            "## Totais por etapa",
            "",
            "| Etapa | Entrada | Saída | Erros |",
            _MD_TABLE_SEP,
        ]
        etapas = execucao.etapas  # type: ignore[attr-defined]
        for etapa in etapas.order_by("ordem_etapa"):
            linhas.append(
                f"| {etapa.nome_etapa}"
                + f" | {etapa.registros_entrada}"
                + f" | {etapa.registros_saida}"
                + f" | {etapa.registros_erro} |"
            )

        linhas += [
            "",
            f"- Extraído: {execucao.total_extraido}",
            f"- Transformado: {execucao.total_transformado}",
            ("- Carregado no Keycloak:" + f" {execucao.total_carregado}"),
            f"- Erros: {execucao.total_erros}",
            f"- Ignorados: {execucao.total_ignorados}",
        ]

        if usuarios_sistema:
            linhas += [
                "",
                "## Usuários do sistema",
                "",
                "| Login | Nome | Grupos | Keycloak |",
                _MD_TABLE_SEP,
            ]
            for login in sorted(usuarios_sistema):
                dados_coresso = usuarios_sistema[login]
                kc_info = validacoes_kc.get(login, {})
                nome = kc_info.get("nome") or dados_coresso.get("nome") or "—"
                grupos = dados_coresso.get("grupos", "—")
                col_kc = _col_keycloak(kc_info, realm)
                linhas.append(
                    f"| {login}"
                    + f" | {nome}"
                    + f" | {grupos}"
                    + f" | {col_kc} |"
                )
        else:
            linhas += [
                "",
                ("## Amostra por fonte" + " (para conferência manual)"),
                "",
            ]
            linhas += _linhas_amostra_por_fonte(
                id_exec, realm, validacoes_kc, None
            )

        caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def _col_keycloak(info: dict[str, Any], realm: str) -> str:
    """Formata a coluna Keycloak do relatório."""
    encontrado = info.get("encontrado", False)
    kc_user_id = info.get("kc_user_id")
    if encontrado and kc_user_id:
        url = _url_usuario_kc(kc_user_id, realm)
        return f"[✅ ver]({url})"
    if encontrado:
        return "✅"
    return "❌"


def _identificadores_usuario(usuario: Any) -> set[str]:
    """Retorna todos os identificadores possíveis."""
    ids: set[str] = set()
    cpf = "".join(c for c in (usuario.cpf or "") if c.isdigit())
    if cpf:
        ids.add(cpf)
    rf = (getattr(usuario, "rf", None) or "").strip()
    if rf:
        ids.add(rf)
    mat = (getattr(usuario, "matricula", None) or "").strip()
    if mat:
        ids.add(mat)
    return ids


def _filtrar_usuarios(
    id_exec: str,
    modelo: Any,
    logins_sistema: set[str] | None,
) -> list[Any]:
    """Filtra usuários por logins do sistema."""
    todos = modelo.objects.filter(id_execucao=id_exec).order_by("id")
    if not logins_sistema:
        return list(todos)
    return [u for u in todos if _identificadores_usuario(u) & logins_sistema]


def _linhas_amostra_por_fonte(
    id_exec: str,
    realm: str,
    validacoes_kc: dict[int, dict[str, Any]],
    logins_sistema: set[str] | None,
) -> list[str]:
    """Gera as linhas MD da amostra por fonte."""
    linhas: list[str] = []
    for fonte, modelo in _FONTES_MODELO:
        usuarios = _filtrar_usuarios(id_exec, modelo, logins_sistema)
        if not usuarios:
            continue
        linhas += [
            f"### {fonte}",
            "",
            ("| RF/CPF/Matrícula | Nome | Situação" + " | Keycloak |"),
            _MD_TABLE_SEP,
        ]
        for usuario in usuarios:
            identificador = _identificador_exibicao(usuario)
            uid = usuario.id  # type: ignore[union-attr]
            info = validacoes_kc.get(uid, {})
            linhas.append(
                f"| {identificador}"
                + f" | {usuario.nome or '—'}"
                + f" | {usuario.situacao or '—'}"
                + f" | {_col_keycloak(info, realm)} |"
            )
        linhas.append("")
    return linhas
