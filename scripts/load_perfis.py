"""
Módulo responsável pela sincronização de perfis do Staging com o Keycloak.

Este script processa registros da tabela StagingPerfilCoreSSO e realiza a
criação, atualização ou validação de roles no Keycloak, utilizando o client
administrativo do realm informado.

Também realiza controle de progresso, contabilização de ações e captura de erros
durante o processamento em lote.
"""
import os
import sys
import time

import django

from core.keycloak_client import get_admin_client, upsert_kc_client_role
from staging.models import StagingPerfilCoreSSO

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "etl_ms.settings")
django.setup()

ACTION_MAP = {
    "created": "created",
    "updated": "updated",
}

def resolve_action_counts(counts: dict, action: str):
    """
    Atualiza o contador de ações com base no resultado retornado pelo Keycloak.

    Args:
        counts (dict): Dicionário contendo os contadores de created, updated,
            skipped e errors.
        action (str): Ação retornada pelo upsert ('created', 'updated' ou outro).

    Returns:
        None
    """
    if action in ACTION_MAP:
        counts[action] += 1
    else:
        counts["skipped"] += 1

def log_progress(idx, total, t0, created, updated, skipped, errors, realm):
    """
    Exibe o progresso atual do processamento em lote.

    Args:
        idx (int): Índice atual do item processado.
        total (int): Total de registros a processar.
        t0 (float): Timestamp inicial da execução.
        created (int): Total de itens criados.
        updated (int): Total de itens atualizados.
        skipped (int): Total de itens ignorados.
        errors (int): Total de erros encontrados.
        realm (str | None): Realm do Keycloak em uso.

    Returns:
        None
    """
    elapsed = time.time() - t0
    print(
        f"  [{idx}/{total}] elapsed={elapsed:.1f}s "
        f"created={created} updated={updated} skipped={skipped} "
        "errors={errors} (realm={realm})",
        flush=True,
    )

def handle_error(errs: list, p, exc: Exception):
    """
    Registra erros ocorridos durante o processamento de um perfil.

    Mantém apenas os 10 primeiros erros para evitar crescimento excessivo da lista.

    Args:
        errs (list): Lista de erros coletados.
        p (StagingPerfilCoreSSO): Instância do perfil processado.
        exc (Exception): Exceção capturada.

    Returns:
        None
    """
    if len(errs) < 10:
        errs.append((p.kc_role_name, str(exc)[:140]))

def process_profile(admin, p, counts, errs):
    """
    Processa um perfil individual e executa upsert no Keycloak.

    Args:
        admin: Cliente administrativo do Keycloak.
        p (StagingPerfilCoreSSO): Perfil a ser processado.
        counts (dict): Contadores de execução (created, updated, skipped, errors).
        errs (list): Lista de erros capturados.

    Returns:
        None
    """
    try:
        r = upsert_kc_client_role(admin, p)
        resolve_action_counts(counts, r["action"])
    except Exception as e:
        counts["errors"] += 1
        handle_error(errs, p, e)

def main(sis_id: int | None = None, realm: str | None = None):
    """
    Executa o processamento em lote dos perfis do Staging para o Keycloak.

    Este método:
    - Filtra os registros opcionais por sis_id
    - Itera sobre os perfis do staging
    - Executa criação/atualização no Keycloak
    - Mantém estatísticas de execução
    - Exibe logs de progresso

    Args:
        sis_id (int | None): ID do sistema para filtro opcional.
        realm (str | None): Realm do Keycloak a ser utilizado.

    Returns:
        None
    """
    qs = StagingPerfilCoreSSO.objects.select_related("sistema").all()

    if sis_id is not None:
        qs = qs.filter(coresso_sis_id=sis_id)

    total = qs.count()
    print(f"Total perfis: {total} (realm={realm})", flush=True)

    admin = get_admin_client(realm=realm)

    counts = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
    }

    errs = []
    t0 = time.time()

    for idx, p in enumerate(qs.iterator()):
        if idx and idx % 50 == 0:
            log_progress(
                idx, total, t0,
                counts["created"],
                counts["updated"],
                counts["skipped"],
                counts["errors"],
                realm,
            )
            admin = get_admin_client(realm=realm)

        process_profile(admin, p, counts, errs)

    elapsed = time.time() - t0
    print(
        f"DONE in {elapsed:.1f}s: created={counts['created']} "
        f"updated={counts['updated']} skipped={counts['skipped']} "
        f"errors={counts['errors']}",
        flush=True,
    )

    if errs:
        print("First errors:")
        for name, msg in errs:
            print(f"  - {name}: {msg}")


if __name__ == "__main__":
    sis = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != "-" else None
    realm = sys.argv[2] if len(sys.argv) > 2 else None
    main(sis, realm)
