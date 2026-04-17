# DLP-1.4 — Duo-Link Pidgin (protocolo de coordenação entre agentes)

Evolução do DLP-1.3. Mantém essência e acrescenta robustez para logs, parsing, handoffs repetidos e automação.

## Mudanças 1.3 → 1.4

1. **Header em uma linha, corpo opcional em linhas seguintes** (separação sintática real)
2. **Campo `X:` de correlação** (correlation id)
3. **Performativo `CANCEL`** (invalidação explícita)
4. **Semântica de `D:` endurecida** (prefere relativo; absoluto HH:MM só com contexto claro; ISO-8601 quando houver ambiguidade)
5. **Obrigatoriedade contextual de `E` e `N`** (validada pelo linter)
6. **`S:` limitado a estado de execução** (resultado vai em `E:`)
7. **Linter com coerência operacional** (regras por performativo)

1.4 é backward-compatible com 1.3 via modo `compat` do linter (`C:` monolinha aceito).

## Formato

### Header (linha 1, obrigatório)

```
P:<perf> B:<owner> D:<deadline> U:<urgency> A:<action> T:<target> [X:<corr-id>] S:<state> [E:<evidence>] [N:<next>]
```

Campos obrigatórios: `P, B, D, A, T, S`.
Opcionais: `U, X, E, N`.

### Corpo (linhas 2+, opcional — linguagem natural)

Texto livre multilinha; não interfere com parsing do header.

### Exemplo

```
P:DO B:you D:+5m U:mid A:check T:day3-files X:d3-01 S:run N:report
Verifica calendário e estado dos arquivos do dia 3.
```

## Campos

### `P:` — performative
`DO, TELL, ACK, DONE, ERR, HOLD, CLOSE, CANCEL, PLAN`

- `DO` — pedido
- `TELL` — informação / status
- `ACK` — reconhece mensagem
- `DONE` — tarefa concluída
- `ERR` — erro / bloqueio
- `HOLD` — espera declarada
- `CLOSE` — encerrar sessão
- `CANCEL` — tarefa invalidada (contexto mudou, não é erro nem conclusão) **[1.4]**
- `PLAN` — proposta de plano

### `B:` — ball (ownership)
`me, you, none, both`

Em logs persistentes preferir identificadores absolutos quando houver risco de replay (ver #1 do spec; opcional no 1.4).

### `D:` — deadline
Hierarquia recomendada:

1. **Relativo (preferido em curto ciclo)**: `+5m`, `+45m`, `+2h`
2. **Absoluto contextual** (apenas quando data/fuso inequívocos): `23:32`
3. **ISO-8601 completo** (quando houver ambiguidade temporal): `2026-04-17T23:32-03:00`
4. `none` — sem prazo

Linter **warning** em `HH:MM` se contexto não for obviamente síncrono; **erro** só em formatos fora da gramática.

### `U:` — urgency
`low, mid, high`

### `A:` — action
Verbo curto kebab-case: `check`, `test`, `publish`, `patch`, `navigate`.

### `T:` — target
Substantivo curto kebab-case: `day3-files`, `preflight`, `dlp-spec`.

### `X:` — correlation id **[1.4, opcional]**
ID curto estável para correlacionar pedido↔resposta em ciclos repetidos.

Formato recomendado: `<prefixo>-<NN>`, ex: `pf-002`, `d3-01`.

Linter: se `X:` aparece em pedido, respostas correlatas **devem** preservar o mesmo id.

### `S:` — state **[1.4: puro, só execução]**
`run, wait, blocked, done, skip`

Resultado (`ok`, `partial`, métricas) vai em `E:`.

### `E:` — evidence (opcional / contextual)
Valor curto sem espaços. Use `-` ou `_` para compor. Pode usar `;` para múltiplos pares:

```
E:3-tests-pass
E:result=ok;tests=3
```

### `N:` — next (opcional / contextual)
Próxima ação esperada, kebab-case curto: `report`, `retest`, `publish`, `ack`.

### `C:` — content (legado 1.3, opcional)
Monolinha até 120 chars. Aceito só em modo `compat`. Em `strict` use o corpo multilinha.

## Obrigatoriedade contextual (linter) **[1.4]**

| Performativo | Exige |
|---|---|
| `DO` | `N:` fortemente recomendado |
| `ERR` | `E:` e `N:` obrigatórios; `S:blocked` |
| `DONE` | `E:` obrigatório |
| `HOLD` | `N:` obrigatório; `D:` ≠ `none` |
| `CLOSE` | `E:` obrigatório (`window-elapsed`, `mutual-consent`, `user-release`); `B:none` |
| `CANCEL` | `E:` obrigatório (motivo, ex: `superseded-by-new-input`) |
| `ACK` | curto; sem `C:` longo |

## Regras de valores

- Sem espaços em valores de campos protocolares.
- Compostos curtos: kebab (`3-tests-pass`), snake (`logged_in=true`).
- Linguagem natural → corpo multilinha, não dentro dos campos.
- `C:` (legado), se presente, deve ser o último e consumir o resto da linha.

## Perfis

### `DLP-ops/strict` (produção, lint, integração)
- Header obrigatório em linha 1
- Ordem canônica: `P B D U A T [X] S [E] [N]`
- Sem espaços em valores
- Corpo em linha separada
- `C:` **não** aceito (só strict)

### `DLP-ops/compat` (adoção incremental)
- Aceita `C:` monolinha no final
- Tolera ordem flexível dentro do header
- Sempre conversível para strict

## Exemplos canônicos

### Pedido
```
P:DO B:you D:+5m U:mid A:check T:day3-files X:d3-01 S:run N:report
Verifica calendário e estado dos arquivos do dia 3.
```

### Status parcial
```
P:TELL B:you D:none U:mid A:check T:day3-files X:d3-01 S:run E:2-of-3-ok N:retest
Falta confirmar o último arquivo.
```

### Erro
```
P:ERR B:me D:+10m U:high A:parse T:dlp-spec X:spec-07 S:blocked E:invalid-token N:retry
Parser falhou em campo fora da ordem.
```

### Conclusão com handoff
```
P:DONE B:you D:+5m U:mid A:test T:preflight X:pf-002 S:done E:3-tests-pass N:publish
Testes executados; pronto para publicação.
```

### Cancelamento
```
P:CANCEL B:none D:none U:mid A:end T:preflight X:pf-002 S:done E:superseded-by-new-input N:none
Tarefa substituída por novo fluxo.
```

### Fechamento de sessão
```
P:CLOSE B:none D:none U:low A:end T:session S:done E:window-elapsed
Sessão 20:09-20:19 encerrada.
```

## Migração de 1.3

- Mensagens 1.3 com `C:` continuam válidas em modo `compat`.
- Para strict: mover `C:` pra corpo (linha 2+).
- `P:CANCEL` e `X:` são aditivos — não quebram nada.
- `S:ok` e `S:partial` de 1.3 → em 1.4, mover para `E:` (ex: `S:done E:result=ok`).
