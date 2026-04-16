# duo-link

`duo-link` e uma ferramenta CLI para coordenacao local entre agentes que compartilham o mesmo workspace.

Ela nasceu de um caso real: dois agentes CLI, em terminais diferentes, precisavam sair de mensagens ad hoc e passar a trabalhar em dupla com um canal auditavel, simples de automatizar e nativo de terminal.

## O que o duo-link resolve

Quando dois agentes dividem o mesmo diretorio, normalmente falta um meio simples de:

- trocar mensagens sem depender da sessao do usuario
- registrar quem assumiu qual parte do trabalho
- manter backlog e contexto por agente
- recuperar historico depois de timeouts, reinicios ou trocas de sessao

O `duo-link` resolve isso com um canal local baseado em arquivos e JSONL, com comandos pequenos e previsiveis.

## Quando usar

Use `duo-link` quando:

- dois agentes CLI compartilham o mesmo workspace
- voce quer coordenacao auditavel por terminal
- precisa de backlog, historico e contexto local
- quer um canal que continue funcionando mesmo sem TUI compartilhada

## Quando nao usar

Nao e a melhor escolha quando:

- ha apenas um agente e o usuario ja esta coordenando tudo manualmente
- os agentes nao compartilham o mesmo filesystem
- o caso exige throughput alto, baixa latencia extrema ou transporte de rede
- o problema principal e distribuicao remota, autenticacao ou criptografia

## O que a ferramenta faz hoje

- cria e descobre um canal local compartilhado
- envia e recebe mensagens entre agentes por CLI
- acompanha novas mensagens em tempo real
- mantem historico append-only em JSONL
- guarda contexto resumido por agente em arquivos separados
- oferece um `repl` simples para conversa interativa
- faz ACK formal de mensagens com contagem de pendentes
- isola trafego com sessoes nomeadas
- correlaciona mensagens com `reply_to`
- drena backlog com `drain` e inspeciona backlog com `pending`
- exporta e importa historico em JSONL
- faz `purge` e `rotate` do log
- expõe `stats` por agente, prioridade e tipo
- filtra `history`, `recv` e `drain` por remetente, destinatario, sessao, prioridade, tipo e `reply_to`

Arquivos principais do canal:

- `chat.log`
- `.chat.lock`
- `.acks`
- `.cursor.*`
- `.consumed.*`
- `context.<agent>.md`

## Requisitos

- Python 3.10+
- Linux ou ambiente POSIX
- `inotifywait` e opcional, mas melhora a entrega em tempo real

Sem `inotifywait`, a ferramenta cai para polling.

## Instalacao

Uso local no checkout:

```bash
PYTHONPATH=src python3 -m duo_link_cli status
```

Instalacao editable para desenvolvimento:

```bash
python3 -m pip install -e .
duo-link status
```

Se o ambiente vier sem `setuptools` na venv, pode ser necessario usar uma venv com `--system-site-packages` e `--no-build-isolation`.

## Inicio rapido

Criar um canal:

```bash
duo-link init --channel /tmp/duo-demo
```

Enviar uma mensagem:

```bash
duo-link send --as codex --channel /tmp/duo-demo claude "ola"
```

Receber a proxima mensagem:

```bash
duo-link recv --as claude --channel /tmp/duo-demo --timeout 30
```

Ver historico recente:

```bash
duo-link history --as codex --channel /tmp/duo-demo -n 20
```

Ver estado do canal:

```bash
duo-link status --channel /tmp/duo-demo --json
```

## Fluxo real entre agentes

Exemplo de coordenacao entre `codex` e `claude` no mesmo workspace.

1. Inicializacao do canal

```bash
duo-link init --channel /tmp/duo-demo
```

2. Cada agente registra seu contexto resumido

```bash
duo-link context set --channel /tmp/duo-demo codex --text "Estou cuidando de testes e contrato."
duo-link context set --channel /tmp/duo-demo claude --text "Estou cuidando de implementacao e cleanup."
```

3. Claude assume uma frente

```bash
duo-link send --as claude --channel /tmp/duo-demo codex "Peguei a parte de implementacao do parser."
```

4. Codex responde com ownership complementar

```bash
duo-link send --as codex --channel /tmp/duo-demo claude "Eu fico com testes e validacao dessa rodada."
```

5. Claude consome backlog e responde

```bash
duo-link recv --as claude --channel /tmp/duo-demo --timeout 30
duo-link send --as claude --channel /tmp/duo-demo codex "Fechei a implementacao. Pode revalidar."
```

6. Codex drena backlog, revalida e fecha a rodada

```bash
duo-link drain --as codex --channel /tmp/duo-demo --json
duo-link send --as codex --channel /tmp/duo-demo claude "Suite verde. Rodada fechada."
```

Esse fluxo foi o caso real que originou o projeto: dois agentes CLI, com ownership explicito, handoff por terminal e backlog confiavel.

## Comandos essenciais

- `init`: cria o canal e os arquivos base
- `send`: envia mensagem para outro agente
- `recv`: consome uma mensagem compativel com os filtros informados
- `drain`: consome todas as mensagens pendentes compativeis com os filtros
- `pending`: mostra backlog ainda nao consumido
- `history`: inspeciona historico e aplica filtros
- `context show|set`: le e escreve contexto por agente
- `ack`: confirma recebimento formal de uma mensagem
- `stats`: mostra agregados do canal
- `export` / `import`: backup e migracao do historico
- `purge` / `rotate`: manutencao do log

## Exemplos uteis

Assumir uma thread especifica:

```bash
duo-link send --as codex --channel /tmp/duo-demo claude "Fechei os testes." --reply-to 14
```

Isolar uma sessao de trabalho:

```bash
duo-link send --as claude --channel /tmp/duo-demo --session release codex "Vou cuidar do publish path."
duo-link history --channel /tmp/duo-demo --session release --json
```

Filtrar por prioridade e tipo:

```bash
duo-link send --as codex --channel /tmp/duo-demo --priority urgent --type status claude "Build quebrou."
duo-link recv --as claude --channel /tmp/duo-demo --priority urgent --type status --json
```

Exportar e importar um canal:

```bash
duo-link export --channel /tmp/duo-demo --output backup.jsonl
duo-link import --channel /tmp/duo-demo-copy --input backup.jsonl
```

## Descoberta de canal e identidade

O canal pode ser resolvido por:

1. `--channel`
2. `DUO_CHANNEL` ou `DUO_LINK_DIR`
3. descoberta automatica de `./duo-link/` ou de um `chat.log` ao subir a arvore de diretorios

A identidade do agente pode ser resolvida por:

1. `--as`
2. `DUO_ID` ou `DUO_AGENT`

## Protocolos

O duo-link inclui protocolos opcionais para melhorar a coordenacao entre agentes:

### DLP-1.3 (Duo-Link Pidgin)

Formato de mensagem estruturado com header protocolar + corpo livre opcional:

```text
P:DO B:you D:+5m U:mid A:check T:tests S:run N:report | C:roda testes e me avisa.
```

Campos: `P` (performative), `B` (ownership), `D` (deadline), `U` (urgency), `A` (action), `T` (target), `S` (state), `E` (evidence), `N` (next), `C` (content).

Spec completa: `docs/dlp-1.3-spec.md`
Linter: `src/duo_link_cli/dlp_lint.py`

### Anti-deadlock (8 regras)

Protocolo para evitar que dois agentes fiquem esperando um ao outro. Inclui:
- ownership explicito por mensagem
- deadlines obrigatorios
- janela como contrato (ninguem sai antes sem consenso)
- precedencia do operador sobre topicos em andamento

Documentacao: `docs/anti-deadlock.md`

### Close guard

Script que valida se o encerramento de sessao e permitido:
- `window-elapsed`: janela venceu
- `mutual-consent`: ambos concordaram em sair antes
- `user-release`: operador liberou

Uso: `python3 -m duo_link_cli.close_guard --json`

## Estado do projeto

Hoje o projeto cobre o caso principal muito bem:

- JSONL com IDs por mensagem e compatibilidade com formato legado
- ACK formal
- sessoes nomeadas
- `reply_to`
- `drain`, `pending`, `purge` e `rotate`
- `export` / `import`
- filtros avancados em `history`, `recv` e `drain`
- prioridades e tipos de mensagem
- observabilidade por agente, prioridade e tipo
- CI em Python 3.10/3.11/3.12
- `43` testes cobrindo fluxos felizes e bordas

Backlog futuro, nao prioritario para o caso real que originou a ferramenta:

- backend alternativo por socket Unix
- plugin de transporte customizado

## Resumo franco

Para coordenacao real entre dois agentes CLI no mesmo workspace, o `duo-link` ja esta em um ponto util e pragmatico.

Ele nao tenta ser um barramento de mensagens geral. Ele tenta ser um canal local, auditavel e reproduzivel para trabalho em dupla entre agentes. E hoje isso ja esta resolvido.
