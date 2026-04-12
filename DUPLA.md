# Instrucoes para trabalho em dupla entre agentes CLI

Documento baseado na experiencia real de colaboracao entre Claude Code e Codex no projeto duo-link-cli. Colocar na raiz do proximo projeto.

---

## 1. Bootstrap — Como iniciar

### Passo 1: Inicializar canal na raiz do projeto

```bash
duo-link init
```

Isso cria `./duo-link/` com `chat.log`, `.chat.lock` e arquivos de contexto.

### Passo 2: Cada agente registra contexto

**Terminal Claude Code:**
```bash
duo-link context set claude --text "Claude Code. Opus 4.6. Ferramentas: bash, browser, busca web, agentes em background."
```

**Terminal Codex:**
```bash
duo-link context set codex --text "Codex. GPT-5. Ferramentas: shell sandbox, apply_patch, leitura/escrita de arquivos."
```

### Passo 3: Primeira mensagem com projeto + objetivo + ownership

```bash
duo-link send --as claude codex "Projeto: NOME. Objetivo: DESCRICAO. Proponho: eu cuido de X, voce cuida de Y."
```

O outro agente responde confirmando ou ajustando.

### Passo 4: Verificar que a comunicacao funciona

```bash
duo-link recv --as codex --timeout 30
```

So comece a trabalhar depois de confirmar comunicacao bidirecional.

---

## 2. Como chamar as skills

### Claude Code

A skill `duo-link` ativa automaticamente quando o usuario pede coordenacao. Tambem pode ser chamada com:
```
/duo-link objetivo ou mensagem inicial
```

### Codex

A skill `duo-link` esta em `~/.codex/skills/duo-link/`. Ativa automaticamente quando o usuario pede coordenacao ou menciona o parceiro.

### Ambos

Sem skill, os comandos `duo-link` funcionam direto no bash:
```bash
duo-link send --as AGENTE PARCEIRO "mensagem"
duo-link recv --as AGENTE --timeout 60
```

---

## 3. Divisao de trabalho

### Regra de ouro

**Nunca dois agentes editando o mesmo arquivo ao mesmo tempo.**

### Como dividir

1. Declarar ownership no canal ANTES de comecar a editar
2. Dividir por arquivo, nao por funcao dentro do mesmo arquivo
3. Exemplos que funcionaram:
   - Um escreve testes, outro implementa (TDD cruzado)
   - Um cuida de backend (channel.py), outro de CLI (cli.py)
   - Um cuida de implementacao, outro de documentacao

### Mensagem de ownership

```bash
duo-link send --as claude codex "Vou editar channel.py e tasks.py. Nao toca nesses. Voce fica com tests/ e README."
```

---

## 4. Comunicacao durante o trabalho

### Frequencia

Enviar update no canal a cada entrega parcial, nao esperar terminar tudo.

### Formato de mensagem

Toda mensagem deve ter:
1. **O que fez** — "Implementei cursor persistente em channel.py"
2. **O que espera do parceiro** — "Preciso que voce escreva testes pro novo comportamento"
3. **Proximo passo** — "Depois disso, vou partir pra JSONL"

### Polling

Entre iteracoes de trabalho, verificar se tem mensagem:
```bash
duo-link pending --as claude
```

### Timeout de resposta

Se `recv --timeout 120` der timeout, nao assumir que o parceiro sumiu. Verificar:
```bash
duo-link history -n 5
```

---

## 5. Handoff — Trocando de turno

### Ao terminar uma rodada

```bash
duo-link send --as claude codex "Rodada concluida. Fiz: X, Y, Z. Testes: N/N passando. Proximo passo sugerido: W. Arquivos que toquei: A, B, C."
```

### Ao receber handoff

1. Ler a mensagem do parceiro
2. Rodar testes pra confirmar que esta verde
3. Ler os arquivos que o parceiro editou antes de comecar
4. Responder confirmando que recebeu

---

## 6. Erros que cometemos e como evitar

### Erro 1: Dispersar sem alinhar

**O que aconteceu:** Um agente comecou a trabalhar em varias frentes sem avisar o outro. Resultado: codigo incompativel, retrabalho.

**Como evitar:** Sempre alinhar no canal antes de abrir frente nova. Uma mensagem curta basta.

### Erro 2: Assumir API do parceiro sem ler o codigo

**O que aconteceu:** Eu (Claude) escrevi testes assumindo que `mark_done()` retornava lista, mas o Codex implementou retornando `Task`. Resultado: 16 testes falhando.

**Como evitar:** Antes de escrever codigo que depende do parceiro, ler o arquivo dele:
```bash
# Verificar assinatura real
python3 -c "import inspect; from modulo import Classe; print(inspect.signature(Classe.metodo))"
```

### Erro 3: Paths hardcoded

**O que aconteceu:** Skills com paths fixos do projeto conectar. Em projeto novo, nao funcionaria.

**Como evitar:** Nunca hardcode paths de projeto. Usar auto-discovery do duo-link (`./duo-link/` no cwd).

### Erro 4: Editar o mesmo arquivo simultaneamente

**O que aconteceu:** Ambos editaram cli.py e tasks.py ao mesmo tempo. Resultado: conflitos e file-modified errors.

**Como evitar:** Declarar ownership de arquivos antes de comecar. Dividir por arquivo.

### Erro 5: Nao rodar testes antes de avisar o parceiro

**O que aconteceu:** Avisei que estava pronto mas os testes nao passavam porque nao rodei antes de mandar a mensagem.

**Como evitar:** Sempre rodar testes antes de declarar que terminou:
```bash
python3 -m pytest tests/ -q
```

---

## 7. Padroes que funcionaram

### TDD cruzado

Um agente escreve os testes definindo o contrato. O outro implementa ate os testes passarem. Melhor padrao da sessao inteira.

### Commits frequentes

Commitar apos cada entrega parcial, nao acumular. Permite que o parceiro veja o estado real.

### Mensagens operacionais

Mensagens curtas e objetivas funcionam melhor que mensagens longas. Formato:
```
Fiz X. Testes: N/N. Proximo: Y. Nao toquei em Z.
```

### Autonomia com alinhamento

Trabalhar autonomamente entre alinhamentos, mas alinhar ANTES de comecar e DEPOIS de terminar. Nao precisa pedir permissao pra cada acao.

---

## 8. Task queue — Para trabalho autonomo

Quando o fluxo nao precisa de input do usuario entre etapas:

```bash
# Criar pipeline
duo-link task add --target terminal_a --next-json '{"target":"terminal_b","command":"python","args":["etapa2.py"]}' -- python etapa1.py

# Iniciar workers em cada terminal
duo-link worker run --target terminal_a --name claude-worker
duo-link worker run --target terminal_b --name codex-worker
```

O pipeline roda sozinho ate acabar.

---

## 9. Checklist rapido

Antes de comecar:
- [ ] `duo-link init` na raiz do projeto
- [ ] Contexto registrado por cada agente
- [ ] Primeira mensagem com projeto + objetivo + ownership
- [ ] Comunicacao bidirecional confirmada

Durante o trabalho:
- [ ] Ownership de arquivos declarado no canal
- [ ] Updates apos cada entrega parcial
- [ ] Testes rodados antes de declarar pronto
- [ ] Ler codigo do parceiro antes de depender dele

Ao terminar:
- [ ] Mensagem de handoff com o que fez + proximo passo
- [ ] Testes passando
- [ ] Commit feito
