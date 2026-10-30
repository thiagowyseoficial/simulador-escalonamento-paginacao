# Simulador de escalonamento multiprocessado com paginação sob demanda

Trabalho M2 de Sistemas Operacionais. Simulador em Python (biblioteca padrão)
que integra escalonamento de CPU (FCFS, SJF não preemptivo e Round Robin) com
memória paginada sob demanda (substituição global FIFO ou LRU), em máquinas
com 1 ou 4 CPUs e uma única fila de prontos.

Integrantes: Thiago Wyse, Matheus Souza e Vitor Hugo

## Estrutura

| Arquivo | Função |
|---|---|
| `simulador.py` | O simulador. Lê o JSON da carga, roda e imprime o resumo padronizado (seção 9.1), grava o Gantt 0–200 (9.2) e, com `--log`, o log de estados (9.3). |
| `experimentos.py` | Roda o desenho experimental da seção 11 em paralelo e monta as tabelas resumo (CSV e Markdown) com speedup e eficiência. |
| `graficos.py` | Gera os gráficos e as figuras de Gantt a partir de `resultados/`. |
| `gerar_carga.py` | Gera cargas sintéticas no formato do enunciado (só para testar; os arquivos oficiais vêm do professor). |
| `tests/test_exemplos.py` | Testes automatizados com os exemplos das seções 5.4, 5.5, 7.1 (FCFS, SJF e RR q=2), 7.2 e 7.3. |
| `cargas/` | Arquivos JSON de carga. |
| `resultados/` | Métricas (JSON), Gantts (CSV) e tabelas de cada execução. |
| `figuras/` | Gráficos e Gantts em PNG. |
| `docs/` | Enunciado e relatório (`relatorio.md` e `relatorio.docx`). |

## Instalação

O simulador em si não tem dependências. `matplotlib` é só para os gráficos e
`pytest` para os testes.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Execução

```bash
python simulador.py --arquivo cargas/cenario_A_t0.json --cpus 4 --sched rr --quantum 10 \
                    --subst lru --quadros 256 --custo-troca 1
```

| Argumento | Valores | Padrão |
|---|---|---|
| `--arquivo` | caminho do JSON | obrigatório |
| `--cpus` | inteiro ≥ 1 | 1 |
| `--sched` | `fcfs`, `sjf`, `rr` | `fcfs` |
| `--quantum` | inteiro (obrigatório no RR) | – |
| `--subst` | `fifo`, `lru` | `lru` |
| `--quadros` | inteiro ≥ 1 | 256 |
| `--custo-troca` | inteiro ≥ 0 | 1 |
| `--fault` | duração do page fault | 20 |
| `--log [ARQ]` | grava o log de estados | desligado |
| `--log-max N` | pids listados por conjunto em cada linha do log (0 = todos) | 20 |
| `--log-ate T` | registra o log só até o instante T | até o fim |
| `--gantt ARQ` | caminho do CSV do Gantt | `gantt_<config>.csv` |
| `--gantt-fim` | fim da janela do Gantt | 200 |
| `--json ARQ` | grava as métricas em JSON | – |
| `--processos ARQ` | grava CSV com métricas por processo | – |
| `--saida-dir DIR` | pasta dos arquivos gerados | `.` |

Experimentos completos (20 execuções base + FIFO×LRU + variação de quadros, 60 no total) e figuras:

```bash
python experimentos.py --cargas cargas/cenario_A_t0.json cargas/cenario_B_espacadas.json \
                       --quantums 2 10 50 --quadros-extra 64 1024 --saida resultados
python graficos.py --resultados resultados --saida figuras
python -m pytest -q tests
```

No cenário A a fila de prontos chega a 1000 processos e há centenas de milhares de
eventos; por isso o log lista no máximo `--log-max` pids por conjunto (com o total
entre parênteses) e `--log-ate` permite gravar só o trecho de interesse. Com
`--log-ate 300` o log do cenário A tem poucos MB.

`experimentos.py` aceita `--fifo-lru fcfs/1 rr:10/4 ...` para escolher as
configurações da comparação FIFO×LRU e `--quadros-extra 64 128` para variar
a memória.

## Regras implementadas (seção 6)

Ordem dos eventos num mesmo instante `t`:

1. chegadas e retornos (E/S e page fault) entram na fila em ordem de pid;
2. fim de troca de contexto; no RR, fim de quantum: se há outro pronto o
   processo vai para o fim da fila, senão recebe novo quantum sem custo;
3. verificação da referência dos processos já em execução (faults liberam a CPU);
4. despachos, CPU de menor índice primeiro. Um processo recém-despachado sem
   custo de troca verifica sua referência no mesmo instante; se dá fault a CPU
   volta a ficar livre e despacha o próximo (exemplo 7.2);
5. avanço de `t` para `t+1`: cada CPU acumula 1 em útil, troca ou ocioso.

- Troca de contexto (Convenção A): cobrada ao despachar um processo diferente
  do último que a CPU executou, inclusive no primeiro despacho. O processo é
  contado como "executando" durante a troca; tempo de resposta = instante do
  despacho − chegada.
- Espera = soma dos intervalos na fila de prontos (entrada na fila até o despacho).
- Page fault: bloqueia por `--fault` unidades, a unidade de CPU não é
  consumida, a página é carregada imediatamente num quadro e fica protegida
  até o processo reexecutar a referência. Páginas de processos em E/S ou na
  fila continuam na memória e podem ser vítimas. No término os quadros são liberados.
- Sem vítima (todos os quadros protegidos): o processo fica bloqueado 20
  unidades e tenta de novo. **Interpretação adotada:** a nova tentativa refaz o
  fault (se consegue quadro, espera mais `--fault` unidades pela transferência).
  O fault é contado uma vez; as retentativas aparecem separadas no resumo.
- `--fault 0`: a página é carregada na hora e a execução segue (conta o fault,
  mas não bloqueia). Serve para reproduzir os exemplos "sem memória" do enunciado.
- SJF usa o campo `cpu` (parte restante do burst atual); desempate pela ordem
  de entrada na fila. Não preemptivo.
- Taxa de faults = faults ÷ (acertos + faults), onde cada unidade de CPU
  executada é um acerto.

## Estrutura de dados da memória

Um `OrderedDict` com chave `(pid, página)`. A ordem de inserção é a ordem de
carga (FIFO); no LRU cada acerto move a chave para o fim (`move_to_end`), de
modo que o início é sempre a página com referência mais antiga. A vítima é a
primeira chave não protegida. Acerto, carga e remoção são O(1); a busca da
vítima pula só as páginas protegidas (no máximo uma por processo bloqueado em
fault). Um conjunto `protegidas` e um dicionário `pid → páginas` permitem
liberar os quadros no término em O(páginas do processo).

Fila de prontos: `deque` (FCFS e RR) ou heap `(restante, ordem de entrada)`
(SJF). Bloqueados: heap `(instante de retorno, pid)`. Quando não há nada
pronto nem executando, o relógio salta para o próximo evento. Cada execução
com 1000 processos leva de 0,2 s a 3 s.

## Limitações do modelo

- O SJF conhece o tamanho exato do próximo burst (campo `cpu`). Num sistema
  real isso seria estimado (por exemplo, média exponencial dos bursts anteriores).
- Page fault tem custo fixo e não há fila de disco: bloqueados esperam em paralelo.
- Troca de contexto tem custo fixo e não afeta a memória (sem cache, sem TLB).
- O simulador não modela endereços, offsets nem páginas compartilhadas.
- Não há afinidade de CPU além da regra "mesmo processo na mesma CPU não paga troca".
