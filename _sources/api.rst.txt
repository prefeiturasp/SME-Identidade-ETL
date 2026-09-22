Referência de código
====================

Esta página publica as docstrings dos principais módulos do projeto. Detalhes
de endpoints, status HTTP e contratos externos devem permanecer no OpenAPI
(``/api/v1/docs/``) ou em páginas técnicas específicas.

Controle ETL — Modelos
-----------------------

.. automodule:: apps.controle_etl.models
   :members:

Controle ETL — Serializers
----------------------------

.. automodule:: apps.controle_etl.serializers
   :members:

Controle ETL — Views (API)
-----------------------------

.. automodule:: apps.controle_etl.views

.. autoclass:: apps.controle_etl.views.ExecucoesView

.. autoclass:: apps.controle_etl.views.DetalheExecucaoView

.. autoclass:: apps.controle_etl.views.CancelarExecucaoView

.. autoclass:: apps.controle_etl.views.ControleProvisionamentoView

.. autoclass:: apps.controle_etl.views.ConsultaIdentidadeView

.. autoclass:: apps.controle_etl.views.MarcaDaguaView

.. autoclass:: apps.controle_etl.views.ResetarMarcaDaguaView

.. autoclass:: apps.controle_etl.views.CheckpointsView

.. autoclass:: apps.controle_etl.views.TentativasView

.. autoclass:: apps.controle_etl.views.ResumoExecucoesView

.. autoclass:: apps.controle_etl.views.HealthCheckView

.. autofunction:: apps.controle_etl.views.estatisticas

.. autofunction:: apps.controle_etl.views.extrair_sistemas

.. autofunction:: apps.controle_etl.views.provisionar_sistemas

.. autofunction:: apps.controle_etl.views.listar_sistemas

.. autofunction:: apps.controle_etl.views.extrair_perfis

.. autofunction:: apps.controle_etl.views.provisionar_perfis

.. autofunction:: apps.controle_etl.views.listar_perfis

Controle ETL — Dashboard e Kanban (HTML)
-------------------------------------------

.. autoclass:: apps.controle_etl.views.DashboardView

.. autoclass:: apps.controle_etl.views.KanbanView

Controle ETL — Tasks (Celery)
--------------------------------

.. automodule:: apps.controle_etl.tasks
   :members:

Controle ETL — Orquestrador Keycloak
----------------------------------------

.. automodule:: apps.controle_etl.orquestrador_kc

.. autofunction:: apps.controle_etl.orquestrador_kc.obter_admin_keycloak

.. autofunction:: apps.controle_etl.orquestrador_kc.construir_payload_kc

.. autofunction:: apps.controle_etl.orquestrador_kc.construir_payload_token_ms

.. autofunction:: apps.controle_etl.orquestrador_kc.calcular_hash_conteudo

.. autofunction:: apps.controle_etl.orquestrador_kc.provisionar_usuario_kc

.. autofunction:: apps.controle_etl.orquestrador_kc.provisionar_usuarios_kc_em_paralelo

.. autofunction:: apps.controle_etl.orquestrador_kc.provisionar_client_kc

.. autofunction:: apps.controle_etl.orquestrador_kc.provisionar_role_client_kc

Controle ETL — Cliente token-ms
-----------------------------------

.. automodule:: apps.controle_etl.cliente_token_ms

.. autofunction:: apps.controle_etl.cliente_token_ms.enviar_lote

.. autofunction:: apps.controle_etl.cliente_token_ms.enviar_todos

Controle ETL — Autenticação
-------------------------------

.. automodule:: apps.controle_etl.autenticacao
   :members:

Extração
--------

.. automodule:: apps.extracao.tasks

.. autoclass:: apps.extracao.tasks.RegistroIdentidade
   :members:

.. autofunction:: apps.extracao.tasks.extrair_se1426

.. autofunction:: apps.extracao.tasks.extrair_coresso

.. autofunction:: apps.extracao.tasks.extrair_eol_alunos

.. autofunction:: apps.extracao.tasks.extrair_sistemas_coresso

.. autofunction:: apps.extracao.tasks.extrair_perfis_coresso

.. autofunction:: apps.extracao.tasks.buscar_grupos_coresso_por_login

Staging
-------

.. automodule:: apps.staging.models
   :members:

.. automodule:: apps.staging.tasks

.. autofunction:: apps.staging.tasks.persistir_extracao_staging

.. autofunction:: apps.staging.tasks.transformar_staging

.. autofunction:: apps.staging.tasks.deduplicar_identidades
