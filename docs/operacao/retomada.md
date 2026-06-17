# Retomada após Falha

O pipeline usa `CheckpointEtl` para retomada granular por etapa, sem
necessidade de re-executar as etapas já concluídas.

---

## Regra de retomada

`_atualizar_checkpoint(id_execucao, etapa, pagina, ultimo_id)` é chamado
ao final de cada lote processado. Em caso de falha, a próxima tentativa
da task lê o checkpoint e retoma do lote seguinte ao último salvo.

| Campo | Significado em caso de retomada |
|---|---|
| `etapa` | Nome da task interrompida |
| `pagina_atual` | Número do último lote concluído |
| `ultimo_id_processado` | ID do último registro processado no lote |
| `estado_json` | Estado adicional (ex.: totais parciais) |

---

## Em caso de erro por task

```{graphviz}
digraph G {
    rankdir=TB;
    node [shape=box, style="rounded"];

    RUN  [label="Task executa"];
    ERR  [label="Excecao capturada"];
    LOG  [label="LogEtapaETL\nsituacao=FALHA"];
    RAT  [label="RastreioTentativa\nerro registrado"];
    RET  [label="self.retry()\nbackoff exponencial"];
    MAX  [label="max_retries\nexcedido"];
    FIM  [label="ExecucaoETL\nsituacao=falha"];

    RUN  -> ERR;
    ERR  -> LOG;
    ERR  -> RAT;
    ERR  -> RET;
    RET  -> RUN  [label="tentativa N+1"];
    RET  -> MAX  [label="tentativa > 5"];
    MAX  -> FIM;
}
```

---

## Em caso de sucesso

- `LogEtapaETL.situacao` → `SUCESSO`
- `RastreioTentativa` registra a duração
- `CheckpointEtl` não é removido — permanece disponível para auditoria
- `ExecucaoETL.situacao` → `sucesso` após a última etapa

---

## Backoff exponencial

```
atraso = min(60 × 2^(tentativa - 1), 600)  # segundos
```

| Tentativa | Atraso |
|---|---|
| 1 | 60 s |
| 2 | 120 s |
| 3 | 240 s |
| 4 | 480 s |
| 5 | 600 s |
