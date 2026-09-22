# Watermark Incremental

O watermark evita extração completa a cada execução, buscando apenas
registros alterados após a última execução bem-sucedida.

---

## Modelo

`MarcaDaguaExtracao` em `apps/controle_etl/models.py` — um registro por fonte.

---

## Ciclo de vida

```{graphviz}
digraph G {
    rankdir=LR;
    node [shape=box, style="rounded"];

    INICIO [label="Inicio da extracao"];
    LER    [label="_obter_watermark(fonte)\nretorna ultima_data_referencia"];
    QUERY  [label="SQL Server\n(WHERE data > watermark)"];
    PROC   [label="Processar registros"];
    SALVAR [label="_atualizar_watermark(\n  fonte, ultima_data, total\n)"];

    INICIO -> LER -> QUERY -> PROC -> SALVAR;
}
```

---

## Funções

Definidas em `apps/extracao/tasks.py`:

| Função | Responsabilidade |
|---|---|
| `_obter_watermark(fonte)` | Retorna `ultima_data_referencia` ou `None` se nunca executado |
| `_atualizar_watermark(fonte, data, total_processado, ultima_pagina)` | Persiste o novo watermark via `update_or_create` |

---

## Forçar extração completa

Duas formas de resetar o watermark de uma fonte:

**Via API:**
```bash
POST /api/v1/etl/watermark/se1426/resetar/
X-API-Key: <chave>
```

**Via Django shell:**
```python
from apps.controle_etl.models import MarcaDaguaExtracao
MarcaDaguaExtracao.objects.filter(fonte="se1426").delete()
```

Ou passe `data_referencia` explicitamente ao chamar o comando `executar_etl`
— o valor informado sobrepõe o watermark persistido.

---

## Acumulação de totais

`total_processado` é acumulado a cada execução bem-sucedida da extração,
permitindo auditar o volume histórico por fonte sem consultar o banco de origem.
