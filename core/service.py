import logging

from .keycloak_client import (
    assign_user_client_roles,
    build_kc_payload,
    build_token_ms_payload,
    emit_retroalim,
    get_admin_client,
    upsert_user_to_keycloak,
)
from .models import ETLExecution
from .token_ms_client import send_batch
from staging.models import (
    StagingUsuarioAluno,
    StagingUsuarioServidor,
    StagingUsuarioTerceiro,
)

logger = logging.getLogger(__name__)

USER_MODELS = (
    StagingUsuarioServidor,
    StagingUsuarioAluno,
    StagingUsuarioTerceiro,
)

class KeycloakUpsertService:
    def __init__(
        self,
        cpf=None,
        rf=None,
        realm=None,
        execution_id=None,
        assign_roles=True,
        push_token_ms=True,
    ):
        self.cpf = cpf
        self.rf = rf
        self.realm = realm
        self.execution_id = execution_id
        self.assign_roles = assign_roles
        self.push_token_ms = push_token_ms

        self.cpf_clean = self._clean_cpf(cpf)

    def execute(self):
        usuario = self._find_usuario()

        if not usuario:
            raise ValueError(
                "Nenhum usuário encontrado para o CPF/RF informado."
            )

        execution = self._get_execution(usuario)

        target_realm = (
            self.realm
            or (
                execution.target_realm
                if execution
                else "sme-apps"
            )
        )

        admin = get_admin_client(realm=target_realm)

        result = upsert_user_to_keycloak(
            admin,
            usuario,
            realm=target_realm,
            execution=execution,
        )

        self._update_usuario_status(usuario, result)

        roles_result = self._assign_roles(
            admin,
            usuario,
            result,
        )

        self._emit_retroalim(
            usuario,
            result,
            target_realm,
            roles_result,
        )

        token_ms_result = self._push_token_ms(usuario)

        return {
            "action": result["action"],
            "kc_user_id": result["kc_user_id"],
            "content_hash": result["content_hash"],
            "realm": target_realm,
            "source": usuario.source,
            "usuario_id": str(usuario.id),
            "execution_id": str(usuario.execution_id),
            "kc_payload": build_kc_payload(usuario),
            "token_ms_payload": build_token_ms_payload(usuario),
            "token_ms_result": token_ms_result,
            "client_roles": roles_result,
        }

    @staticmethod
    def _clean_cpf(cpf):
        if not cpf:
            return None

        return "".join(
            c for c in str(cpf)
            if c.isdigit()
        )

    def _find_usuario(self):
        for model_class in USER_MODELS:
            qs = model_class.objects.all()

            if self.cpf_clean:
                qs = qs.filter(cpf=self.cpf_clean)

            if (
                self.rf
                and model_class is StagingUsuarioServidor
            ):
                qs = qs.filter(rf=str(self.rf))

            if self.execution_id:
                qs = qs.filter(
                    execution_id=self.execution_id
                )

            usuario = qs.order_by(
                "-extracted_at"
            ).first()

            if usuario:
                return usuario

        return None

    @staticmethod
    def _get_execution(usuario):
        try:
            return ETLExecution.objects.filter(
                id=usuario.execution_id
            ).first()
        except Exception:
            return None

    @staticmethod
    def _update_usuario_status(usuario, result):
        usuario.status = (
            "loaded"
            if result["action"] != "skipped"
            else "skipped"
        )

        usuario.save(update_fields=["status"])

    def _assign_roles(
        self,
        admin,
        usuario,
        result,
    ):
        if not self.assign_roles:
            return {"skipped": True}

        if not result.get("kc_user_id"):
            return {"skipped": True}

        try:
            login = (
                usuario.rf
                or usuario.cpf
                or ""
            ).strip()

            return assign_user_client_roles(
                admin,
                result["kc_user_id"],
                login,
            )

        except Exception as e:
            logger.exception(
                "assign_user_client_roles falhou: %s",
                e,
            )

            return {"error": str(e)}

    def _emit_retroalim(
        self,
        usuario,
        result,
        target_realm,
        roles_result,
    ):
        try:
            emit_retroalim(
                tipo=self._get_event_type(
                    result["action"]
                ),
                usuario=usuario,
                payload={
                    "kc_user_id": result.get(
                        "kc_user_id"
                    ),
                    "realm": target_realm,
                    "action": result["action"],
                    "roles": roles_result,
                },
            )

        except Exception as e:
            logger.warning(
                "retroalim falhou: %s",
                e,
            )

    @staticmethod
    def _get_event_type(action):
        mapping = {
            "created": "user_created",
            "updated": "user_updated",
        }

        return mapping.get(
            action,
            "role_assigned",
        )

    def _push_token_ms(self, usuario):
        if not self.push_token_ms:
            return {"skipped": True}

        try:
            token_ms_payload = (
                build_token_ms_payload(usuario)
            )

            return send_batch(
                [token_ms_payload],
                execution_id=str(
                    usuario.execution_id
                ),
            )

        except Exception as e:
            logger.exception(
                "token-ms push falhou: %s",
                e,
            )

            return {"error": str(e)}