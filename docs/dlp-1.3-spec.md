# duo-link DLP-1.3

Especificacao teorica e operacional do `DLP-1.3` (`Duo-Link Pidgin 1.3`) para coordenacao entre agentes no `duo-link`.

## Objetivo

Reduzir verbosidade e ambiguidade na comunicacao entre agentes sem perder legibilidade humana no `chat.log`.

O `DLP-1.3` nao substitui lingua natural. Ele adiciona um `header` protocolar curto e parseavel, com `corpo livre` opcional.

Arquitetura conceitual:
- camada 1: `header` fixo para turno, prazo, alvo, acao e estado
- camada 2: `corpo` opcional em NL para nuance, contexto e detalhes

## Principios

- legivel por humano
- parseavel por regex simples
- ownership explicito
- deadline explicito
- adequado para `chat.log` visivel ao operador
- compressao opcional, nunca obrigatoria
- `C:` opcional por default
- valor principal em coordenacao, nao em compressao bruta de texto

## Estrutura base

Ordem fixa dos campos:

```text
P:<perf> B:<owner> D:<deadline> U:<urgency> A:<act> T:<target> S:<state> [E:<evidence>] [N:<next>] [C:<content>]
```

Campos obrigatorios:
- `P`
- `B`
- `D`
- `U`
- `A`
- `T`
- `S`

Campos opcionais:
- `E`
- `N`
- `C`

Separacao:
- campos separados por espaco
- cada campo usa `KEY:valor`
- `C:` fica por ultimo e pode conter texto livre
- sem reordenar campos

## Semantica dos campos

### `P` performative

Vocabulario fechado:
- `DO`: pedido operacional
- `TELL`: status ou informacao
- `ERR`: bloqueio, falha ou risco
- `DONE`: conclusao com handoff
- `CLOSE`: encerramento de sessao
- `ACK`: confirmacao curta de recebimento
- `HOLD`: silencio declarado com retorno previsto

### `B` owner

Vocabulario fechado:
- `me`: a bola fica comigo
- `you`: a bola fica contigo
- `both`: execucao paralela
- `none`: sem ownership pendente

### `D` deadline

Formatos aceitos:
- relativo: `+5m`, `+10m`, `+1h`
- absoluto: `23:32`
- sem prazo: `none`

Regras:
- usar `none` so quando realmente nao houver prazo operacional
- para pedido ou handoff, preferir prazo explicito

### `U` urgency

Vocabulario fechado:
- `low`
- `mid`
- `high`

### `A` action

Vocabulario base inicial:
- `check`, `write`, `patch`, `test`, `wait`, `propose`
- `review`, `measure`, `convert`, `parse`, `lint`
- `reply`, `end`, `recv`

Pode crescer com parcimonia. Evitar sinonimos desnecessarios.

### `T` target

Alvo curto e especifico. Exemplos:
- `day3-files`, `preflight`, `dlp-spec`, `session`

Regra: target deve ser curto, estavel e reconhecivel.

### `S` state

Vocabulario base inicial:
- `run`, `ok`, `wait`, `blocked`, `done`, `partial`

### `E` evidence

Resumo curto de evidencia, output ou fato relevante.
Exemplos: `logged_in=true`, `fix-applied`, `3-tests-pass`

### `N` next

Proximo evento esperado ou proxima acao.
Exemplos: `report`, `publish`, `retest`, `none`

### `C` content

Texto livre opcional em NL.

Regras:
- `C:` sempre por ultimo
- pode ser multilinha
- nao substituir o papel do `header`
- no perfil `DLP-ops`, `C:` deve ter no maximo 1 frase ou ~120 caracteres
- se precisar mais contexto, usar `DLP-explain` ou mandar NL livre separado

## Perfis de uso

### `DLP-ops`

Padrao recomendado para trabalho real.

Regras:
- usar `header` sempre
- preferir `header` puro quando possivel
- `C:` opcional e curto (~120 chars max)

Exemplo:

```text
P:DO B:you D:+5m U:mid A:check T:day3-files S:run N:report | C:verifica calendario e estado.
```

### `DLP-explain`

Perfil para sessoes teoricas, analise e raciocinio com mais contexto.

Regras:
- `header` continua obrigatorio
- `C:` pode ser longo
- nao usar como default em operacao de alta frequencia

### Modo compressed opcional

Permitido apenas com glossario fechado e curto. Nao e o default.
Glossario maximo de 20 aliases. Alias so entra se for frequente e reconhecivel.

## Templates canonicos

### 1. Pedido

```text
P:DO B:you D:+5m U:mid A:<act> T:<target> S:run N:<reply>
```

### 2. Status

```text
P:TELL B:you D:none U:mid A:<act> T:<target> S:<state> [E:<evidence>]
```

### 3. Erro

```text
P:ERR B:me D:+10m U:high A:<act> T:<target> S:blocked E:<cause> N:<retry>
```

### 4. Handoff

```text
P:DONE B:you D:+5m U:mid A:<act> T:<target> S:ok E:<result> N:<next>
```

### 5. Close

```text
P:CLOSE B:none D:none U:low A:end T:session S:done E:<summary>
```

### 6. Ack

```text
P:ACK B:you D:none U:low A:recv T:<target> S:ok
```

### 7. Hold

```text
P:HOLD B:me D:+10m U:low A:wait T:<target> S:run N:<when-back>
```

## Regras de validacao minima

Um linter basico deve checar:
- ordem dos campos
- presenca dos obrigatorios
- vocabulario fechado de `P`, `B`, `U`, `S`
- formato valido de `D`
- `C` apenas no fim
- no perfil `DLP-ops`, `C` com no maximo 120 caracteres
- coerencia basica:
  - `P:DO` nao deveria vir com `B:none`
  - `P:CLOSE` deveria usar `B:none`
  - `P:ERR` deveria usar `S:blocked` ou equivalente

## KPIs corretos

Nao usar `chars por msg` como KPI principal do protocolo.

KPI principal:
- `repair turns`
- `handoff clarity`
- `tempo ate a proxima acao`

KPI secundario:
- tamanho medio da mensagem
- densidade de informacao

## Posicionamento final

- `DLP-ops` como default no trabalho real
- `DLP-explain` para pesquisa, comparacao e sintese
- `compressed` apenas como perfil opcional
- adocao incremental, com medicao real de economia e friccao
