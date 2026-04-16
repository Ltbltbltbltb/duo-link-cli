# Protocolo anti-deadlock (8 regras)

Deadlock = os dois agentes esperando o outro mandar mensagem. Acontece com frequencia se nao seguir estas regras.

## Regras

1. **Quem mandou a ultima msg NAO manda de novo** — espera resposta
2. **Cada pedido declara deadline** ("me responde em 5 min" ou "volto em 10 min")
3. **Prazo venceu → leitura passiva** — consulta `tail -1 chat.log`, NAO manda heartbeat
4. **Nova msg so com conteudo operacional novo** — erro, conclusao, bloqueio ou mudanca de plano
5. **Silencio longo e declarado antes** ("ficarei 10 min sem falar enquanto rodo X")
6. **Cada msg diz quem esta com a bola** ("tua vez de testar" ou "bola comigo, volto em 5")
7. **Janela e contrato — saida so com consenso bilateral**:
   - Se o operador definiu X minutos, AMBOS ficam ate o fim da janela
   - Nenhum agente sai antes sem propor CLOSE E receber ACK do parceiro
   - Se o parceiro nao concordar, ambos continuam ate o fim da janela
   - Quando a janela terminar, os dois saem juntos (CLOSE mutuo)
   - Sair sem consenso e BUG DE PROTOCOLO
8. **Instrucao do operador tem precedencia sobre qualquer topico em andamento**:
   - Se o operador redireciona a prioridade, parar o topico atual e atender
   - Nao continuar thread antiga sem revalidar que ainda eh prioridade

## Hardening de fechamento

`CLOSE` nao deve ser um gesto informal.

Camada minima recomendada:
- Regra 7: janela e contrato
- `E:` obrigatorio em `CLOSE` com `window-elapsed`, `mutual-consent` ou `user-release`
- `close_guard` como pre-check antes de encerrar

Politica:
- `window-elapsed`: a janela venceu, fechamento permitido
- `mutual-consent`: saida antecipada aceita pelos dois
- `user-release`: o operador liberou fechamento antes do fim

Regra de seguranca:
- se houver duvida, errar para o lado de permanecer no canal
- saida tardia por alguns segundos e aceitavel; saida precoce sem consenso e bug de protocolo

## Convencao in-flight

Se um agente estiver executando operacao longa (browser, build, etc.), deve declarar no canal:

```text
P:HOLD B:me D:+Xm U:low A:wait T:<operacao> S:run N:<resultado-esperado>
```

Enquanto essa marca estiver ativa, evitar puxar o parceiro para outra frente sem necessidade.
