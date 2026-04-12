# duo-link

`duo-link` e uma ferramenta CLI para comunicacao local entre agentes que compartilham o mesmo workspace.

O projeto nasceu de um caso real: dois agentes CLI, em terminais diferentes, precisavam sair de mensagens ad hoc e passar a coordenar trabalho de verdade usando um canal simples, reproducivel e orientado a terminal.

## O que a ferramenta faz

- cria e descobre um canal local compartilhado
- envia e recebe mensagens entre agentes por CLI
- acompanha mensagens novas em tempo real
- mantem historico append-only
- guarda contexto resumido por agente em arquivos separados
- oferece um `repl` simples para conversa interativa

O backend atual usa arquivos locais no diretorio do canal:

- `chat.log`
- `.chat.lock`
- `context.<agent>.md`

## Requisitos

- Python 3.10+
- Linux ou ambiente POSIX
- `inotifywait` e opcional, mas melhora a entrega em tempo real

Sem `inotifywait`, a ferramenta cai para polling.

## Instalacao

### Uso local no checkout

```bash
PYTHONPATH=src python3 -m duo_link_cli status
```

### Instalacao editable para desenvolvimento

```bash
python3 -m pip install -e .
duo-link status
```

Se o ambiente nao tiver acesso a rede ou vier sem `setuptools` na venv, pode ser necessario usar uma venv com `--system-site-packages` e `--no-build-isolation`.

## Uso rapido

Inicializar um canal:

```bash
duo-link init
```

Enviar uma mensagem:

```bash
duo-link send --as codex claude "ola"
```

Receber a proxima mensagem para voce:

```bash
duo-link recv --as claude --timeout 30
```

Acompanhar novas mensagens:

```bash
duo-link watch --as codex
```

Ver historico:

```bash
duo-link history --as codex -n 20
```

Ver estado do canal:

```bash
duo-link status --json
```

Ler e escrever contexto:

```bash
duo-link context show codex
duo-link context set codex --text "contexto resumido"
```

Abrir um chat simples:

```bash
duo-link repl --as codex claude
```

## Descoberta de canal e identidade

O canal pode ser resolvido por:

1. `--channel`
2. `DUO_CHANNEL` ou `DUO_LINK_DIR`
3. descoberta automatica de `./duo-link/` ou de um `chat.log` ao subir a arvore de diretorios

A identidade do agente pode ser resolvida por:

1. `--as`
2. `DUO_ID` ou `DUO_AGENT`

## Exemplo de coordenacao real

Terminal A:

```bash
duo-link recv --as codex --channel /tmp/duo-demo --timeout 60
```

Terminal B:

```bash
duo-link send --as claude --channel /tmp/duo-demo codex "peguei a etapa de cleanup"
```

Depois o terminal A responde:

```bash
duo-link send --as codex --channel /tmp/duo-demo claude "eu fico com README e testes"
```

## Estado atual

O projeto esta funcional e passou por uma auditoria tecnica com melhorias aplicadas:

- transporte JSONL com IDs por mensagem (backward compatible com formato legado)
- cursor persistente por agente para consumo confiavel de backlog
- comandos de leitura nao criam canal como side effect
- REPL escopado ao par de agentes
- validacao de inputs (timeout, poll_interval, history -n)
- CI com GitHub Actions (Python 3.10/3.11/3.12)

Ainda nao implementado:

- ACK formal, sessoes nomeadas ou multiplexacao de canais
- rotacao de log
- backend alternativo (socket Unix)

Para o caso de uso que originou a ferramenta, isso foi suficiente para coordenacao real entre agentes CLI.
