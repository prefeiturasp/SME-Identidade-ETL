import os
import sys
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "etl_ms.settings")
django.setup()

from core.keycloak_client import get_admin_client, upsert_kc_client_role
from staging.models import StagingPerfilCoreSSO


def main(sis_id: int | None = None, realm: str | None = None):
    qs = StagingPerfilCoreSSO.objects.select_related("sistema").all()
    if sis_id is not None:
        qs = qs.filter(coresso_sis_id=sis_id)

    total = qs.count()
    print(f"Total perfis: {total} (realm={realm})", flush=True)

    admin = get_admin_client(realm=realm)
    created = updated = skipped = errors = 0
    errs = []
    t0 = time.time()
    for idx, p in enumerate(qs.iterator()):
        if idx and idx % 50 == 0:
            elapsed = time.time() - t0
            print(
                f"  [{idx}/{total}] elapsed={elapsed:.1f}s "
                f"created={created} updated={updated} skipped={skipped} errors={errors}",
                flush=True,
            )
            admin = get_admin_client(realm=realm)
        try:
            r = upsert_kc_client_role(admin, p)
            a = r["action"]
            if a == "created":
                created += 1
            elif a == "updated":
                updated += 1
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            if len(errs) < 10:
                errs.append((p.kc_role_name, str(e)[:140]))

    elapsed = time.time() - t0
    print(
        f"DONE in {elapsed:.1f}s: created={created} updated={updated} "
        f"skipped={skipped} errors={errors}",
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
