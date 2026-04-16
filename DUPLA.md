# Instrucoes para trabalho em dupla entre agentes CLI

Documento baseado na experiencia real de colaboracao entre Claude Code e Codex.
Copiar para a raiz do proximo projeto.

---

## 1. Premissas

- A skill `duo-link` e global (disponivel em qualquer sessao)
- O canal deve ser local ao projeto atual, nunca de outro projeto
- A conversa entre agentes precisa acontecer ANTES de abrir trabalho paralelo
- Nunca tratar o canal como teste tecnico — usar para coordenacao real

## 2. Como chamar a skill

### Claude Code

```text
Use a skill duo-link para coordenar com o Codex neste projeto.
```

Ou com objetivo explicito:

```text
Use a skill duo-link para iniciar colaboracao, alinhar ownership e trabalhar em dupla.
```

Em sessao nova, seja explicito — triggers implicitos podem nao disparar.

### Codex

```text
Use a skill duo-link para coordenar com o Claude Code neste projeto.
```

Ou:

```text
Use a skill duo-link para bootstrap do canal local e divisao de trabalho.
```

### Sem skill (direto no bash)

```bash
duo-link send --as AGENTE PARCEIRO "mensagem"
duo-link recv --as AGENTE --timeout 60
```

## 3. Bootstrap em projeto novo

### Passo 1: Inicializar canal na raiz do projeto

```bash
duo-link init
```

### Passo 2: Registrar contexto de cada agente

```bash
duo-link context set claude --text "Claude Code. Capacidades: bash, browser, busca web, agentes."
duo-link context set codex --text "Codex. Capacidades: shell sandbox, apply_patch, leitura/escrita."
```

### Passo 3: Primeira mensagem com projeto + objetivo + ownership

A primeira mensagem NAO deve ser "oi". Deve conter:

```bash
duo-link send --as claude codex "Projeto: NOME. Objetivo: DESCRICAO. Proponho: eu cuido de X, voce cuida de Y."
```

### Passo 4: Esperar resposta antes de trabalhar

```bash
duo-link recv --as codex --timeout 30
```

So comecar trabalho substancial depois de confirmar comunicacao bidirecional.

## 4. Divisao de trabalho

### Regra de ouro

**Nunca dois agentes editando o mesmo arquivo ao mesmo tempo.**

### Como dividir

- Declarar ownership no canal ANTES de editar
- Dividir por arquivo, nao por funcao dentro do mesmo arquivo
- Padroes que funcionaram:
  - **TDD cruzado**: um escreve testes, outro implementa
  - **Separacao por camada**: um backend, outro CLI/testes
  - **Separacao por tipo**: um implementa, outro documenta

### Mensagem de ownership

```bash
duo-link send --as claude codex "Vou editar channel.py e tasks.py. Nao toca nesses. Voce fica com tests/ e README."
```

## 5. Comunicacao durante o trabalho

### Formato de mensagem

Toda mensagem deve ter 3 partes:
1. **O que fez** — "Implementei cursor persistente em channel.py"
2. **O que espera** — "Preciso que voce escreva testes"
3. **Proximo passo** — "Depois disso, vou partir pra JSONL"

### Polling entre iteracoes

```bash
duo-link pending --as claude
```

### Timeout de resposta

Se `recv` der timeout, nao assumir que o parceiro sumiu:
```bash
duo-link history -n 5
duo-link pending --as claude
```

## 6. Handoff

### Ao terminar rodada

```bash
duo-link send --as claude codex "Rodada concluida. Fiz: X, Y. Testes: N/N. Arquivos tocados: A, B. Proximo passo: Z."
```

### Ao receber handoff

1. Ler a mensagem do parceiro
2. Rodar testes: `python3 -m pytest tests/ -q`
3. Ler os arquivos que o parceiro editou
4. Responder confirmando

## 7. Erros que cometemos e como evitar

### Erro 1: Dispersar sem alinhar

Um agente comecou varias frentes sem avisar. Resultado: codigo incompativel.

**Regra:** Falar no canal antes de abrir frente nova.

### Erro 2: Assumir API do parceiro sem ler o codigo

Testes escritos assumindo assinatura errada. 16 testes falharam.

**Regra:** Antes de depender do codigo do parceiro:
```bash
python3 -c "import inspect; from modulo import Classe; print(inspect.signature(Classe.metodo))"
```

### Erro 3: Confundir teste do canal com trabalho real

Parte da interacao virou smoke test em vez de coordenacao.

**Regra:** A primeira conversa deve conter objetivo, ownership e proximo passo.

### Erro 4: Voltar pro usuario cedo demais

Retorno ao usuario antes de troca real entre agentes.

**Regra:** So reportar depois de comunicacao util. Usar timeouts maiores.

### Erro 5: Paths hardcoded nas skills

Skills apontando pra canal de projeto antigo.

**Regra:** Skill global usa projeto atual. Ordem: `--channel` > env > auto-discovery > `duo-link init`.

### Erro 6: Contratos desalinhados em implementacao paralela

Dois agentes implementaram a mesma funcao com APIs diferentes.

**Regra:** Antes de frentes paralelas, combinar ownership, contrato e arquivos.

### Erro 7: Nao rodar testes antes de avisar

Declarou pronto mas testes nao passavam.

**Regra:** Sempre rodar testes antes de mandar handoff:
```bash
python3 -m pytest tests/ -q
```

## 8. Padroes que funcionaram

- **TDD cruzado**: melhor padrao da sessao. Um define contrato via testes, outro implementa
- **Commits frequentes**: apos cada entrega parcial
- **Mensagens operacionais**: curtas, com fez/espera/proximo
- **Autonomia com alinhamento**: trabalhar sozinho entre alinhamentos, mas alinhar antes e depois

## 9. Task queue autonoma

Quando o fluxo nao precisa de input do usuario entre etapas:

```bash
duo-link task add --target terminal_a --next-json '{"target":"terminal_b","command":"python","args":["etapa2.py"]}' -- python etapa1.py

duo-link worker run --target terminal_a --name claude-worker
duo-link worker run --target terminal_b --name codex-worker
```

## 10. Checklist

Antes de comecar:
- [ ] `duo-link init` na raiz do projeto
- [ ] Contexto registrado por cada agente
- [ ] Primeira mensagem com projeto + objetivo + ownership
- [ ] Comunicacao bidirecional confirmada

Durante:
- [ ] Ownership declarado no canal
- [ ] Updates apos cada entrega
- [ ] Codigo do parceiro lido antes de depender dele
- [ ] Testes rodados antes de declarar pronto

Ao finalizar:
- [ ] Handoff com o que fez + proximo passo
- [ ] Testes passando
- [ ] Commit feito
- [ ] Parceiro avisado no canal

## 11. Regra final

Se houver duvida entre "sair fazendo rapido" e "alinhar primeiro":

**Escolha alinhar primeiro.**

Os maiores ganhos vieram quando o canal era coordenacao real, ownership era explicito, e cada rodada terminava com proximo passo combinado.
