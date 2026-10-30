#!/usr/bin/env python3
"""
Simulador de escalonamento multiprocessado com paginação sob demanda.
Equipe: Thiago Wyse, Matheus Souza e Vitor Hugo.

Implementa as regras da seção 6 do enunciado:
  - tempo discreto, fila de prontos única e global, N CPUs;
  - escalonadores FCFS, SJF (não preemptivo) e RR (quantum);
  - troca de contexto com custo fixo (Convenção A);
  - memória paginada global com substituição FIFO ou LRU e page fault de
    duração fixa (padrão 20), com proteção da página até ser reexecutada.

Uso:
  python simulador.py --arquivo carga.json --cpus 4 --sched rr --quantum 10 \
                      --subst lru --quadros 256 --custo-troca 1 [--log] [--gantt g.csv]
"""
from __future__ import annotations

import argparse
import csv
import heapq
import json
import os
import sys
from collections import OrderedDict, deque
from itertools import islice

# ----------------------------------------------------------------------------
# Estados dos processos e das CPUs
# ----------------------------------------------------------------------------
NOVO, PRONTO, EXECUTANDO, BLOQUEADO, TERMINADO = "novo", "pronto", "executando", "bloqueado", "terminado"
CPU_LIVRE, CPU_TROCA, CPU_EXEC = 0, 1, 2


class Processo:
    __slots__ = (
        "pid", "chegada", "classe", "paginas", "bursts", "idx", "pos", "estado",
        "espera", "entrada_fila", "primeiro_despacho", "termino", "faults",
        "retorno", "fault_pendente", "seq", "quantum_restante", "cpu_total",
        "es_total", "preempcoes", "motivo",
    )

    def __init__(self, d: dict):
        self.pid = int(d["pid"])
        self.chegada = int(d["chegada"])
        self.classe = d.get("classe", "")
        self.paginas = int(d["paginas"])
        self.bursts = d["bursts"]
        self.idx = 0            # índice do burst atual (sempre aponta para um burst de CPU quando pronto/executando)
        self.pos = 0            # unidades já executadas do burst de CPU atual
        self.estado = NOVO
        self.espera = 0
        self.entrada_fila = None
        self.primeiro_despacho = None
        self.termino = None
        self.faults = 0
        self.retorno = None     # instante de retorno quando bloqueado
        self.fault_pendente = None  # página aguardando quadro (caso "sem vítima")
        self.seq = 0            # ordem de entrada na fila (desempate do SJF)
        self.quantum_restante = 0
        self.preempcoes = 0
        self.motivo = None      # motivo do bloqueio atual: 'es' ou 'fault'
        self.cpu_total = sum(b["cpu"] for b in self.bursts if "cpu" in b)
        self.es_total = sum(b["es"] for b in self.bursts if "es" in b)
        self._validar()

    def _validar(self):
        """Rejeita cargas fora do formato da seção 5.2 com uma mensagem clara (em vez de travar depois)."""
        erro = lambda msg: ValueError(f"pid {self.pid}: {msg}")
        if self.chegada < 0:
            raise erro(f"chegada {self.chegada} negativa")
        if self.paginas < 1:
            raise erro("paginas deve ser >= 1")
        if not self.bursts or "cpu" not in self.bursts[0] or "cpu" not in self.bursts[-1]:
            raise erro("bursts devem começar e terminar com burst de CPU")
        for i, b in enumerate(self.bursts):
            esperado = "cpu" if i % 2 == 0 else "es"     # alternância cpu, es, cpu, ...
            if esperado not in b:
                raise erro(f"burst {i} deveria ser de {esperado} (os bursts alternam CPU e E/S)")
            if esperado == "cpu":
                if b["cpu"] < 1:
                    raise erro(f"burst {i} tem cpu={b['cpu']}; deve ser >= 1")
                if len(b.get("refs", [])) != b["cpu"]:
                    raise erro(f"burst {i} tem cpu={b['cpu']} mas {len(b.get('refs', []))} refs")
                for r in b["refs"]:
                    if not 0 <= r < self.paginas:
                        raise erro(f"referência à página {r} fora de 0..{self.paginas - 1}")
            elif b["es"] < 0:
                raise erro(f"burst {i} tem es={b['es']} negativo")

    def burst(self) -> dict:
        return self.bursts[self.idx]

    def restante(self) -> int:
        """Parte que falta do burst de CPU atual (usado pelo SJF)."""
        return self.bursts[self.idx]["cpu"] - self.pos

    def pagina_atual(self) -> int:
        return self.bursts[self.idx]["refs"][self.pos]


# ----------------------------------------------------------------------------
# Memória física
# ----------------------------------------------------------------------------
class Memoria:
    """
    N quadros globais. Cada quadro guarda a identidade de uma página (pid, nº).

    Estrutura: um OrderedDict chave=(pid, pagina). A ordem de inserção é a
    ordem de carga (FIFO); no LRU, cada acerto move a chave para o fim
    (move_to_end), de modo que o início do dicionário é sempre a página com
    a referência mais antiga. A vítima é a primeira chave não protegida.
    Operações de acerto, carga e remoção são O(1); a busca da vítima pula
    as páginas protegidas (no máximo uma por processo que sofreu fault e ainda
    não reexecutou a referência, o que inclui o tempo que ele passa na fila de
    prontos depois de voltar). Com fila longa e poucos quadros, como no cenário
    A, a memória pode ficar inteira protegida; aí carregar() devolve False e o
    processo cai na regra "sem vítima". O valor guardado em cada chave (instante de carga) é
    apenas informativo, para depuração; a ordem das chaves é o que importa.
    """

    def __init__(self, quadros: int, politica: str):
        if quadros < 1:
            raise ValueError("--quadros deve ser >= 1")
        self.quadros = quadros
        self.politica = politica
        self.paginas: OrderedDict = OrderedDict()   # (pid, pag) -> instante de carga
        self.protegidas: set = set()                # carregadas por fault ainda não reexecutado
        self.paginas_por_pid: dict = {}             # pid -> conjunto de chaves, para liberar no término
        self.substituicoes = 0

    def ocupados(self) -> int:
        return len(self.paginas)

    def presente(self, chave) -> bool:
        return chave in self.paginas

    def acerto(self, chave):
        if self.politica == "lru":
            self.paginas.move_to_end(chave)
        self.protegidas.discard(chave)   # o processo reexecutou a referência: fim da proteção

    def carregar(self, chave, t: int) -> bool:
        """Traz a página para um quadro. Retorna False se todos os quadros estão protegidos."""
        if len(self.paginas) < self.quadros:
            self._inserir(chave, t)
            return True
        if len(self.protegidas) >= len(self.paginas):   # todos os quadros protegidos: não há vítima
            return False
        for vitima in self.paginas:           # início = carregada há mais tempo (FIFO) / usada há mais tempo (LRU)
            if vitima not in self.protegidas:
                del self.paginas[vitima]
                self.paginas_por_pid[vitima[0]].discard(vitima)
                self.substituicoes += 1
                self._inserir(chave, t)
                return True
        return False

    def _inserir(self, chave, t):
        self.paginas[chave] = t
        self.protegidas.add(chave)           # protegida até o processo reexecutar a referência
        self.paginas_por_pid.setdefault(chave[0], set()).add(chave)

    def liberar(self, pid: int):
        for chave in self.paginas_por_pid.pop(pid, ()):
            del self.paginas[chave]
            self.protegidas.discard(chave)


# ----------------------------------------------------------------------------
# Fila de prontos
# ----------------------------------------------------------------------------
class FilaProntos:
    def __init__(self, sched: str):
        self.sched = sched
        self.seq = 0
        self.fila = [] if sched == "sjf" else deque()
        self.tamanho_max = 0

    def __len__(self):
        return len(self.fila)

    def inserir(self, p: Processo, t: int):
        p.estado = PRONTO
        p.entrada_fila = t
        self.seq += 1
        p.seq = self.seq
        if self.sched == "sjf":
            heapq.heappush(self.fila, (p.restante(), p.seq, p))
        else:
            self.fila.append(p)
        if len(self.fila) > self.tamanho_max:
            self.tamanho_max = len(self.fila)

    def retirar(self) -> Processo:
        if self.sched == "sjf":
            return heapq.heappop(self.fila)[2]
        return self.fila.popleft()

    def pids(self, max_itens=None):
        """Pids em ordem de despacho; no máximo max_itens (None = todos)."""
        if self.sched == "sjf":
            itens = heapq.nsmallest(max_itens, self.fila) if max_itens else sorted(self.fila)
            return [p.pid for _, _, p in itens]
        return list(islice((p.pid for p in self.fila), max_itens))


# ----------------------------------------------------------------------------
# Simulador
# ----------------------------------------------------------------------------
class Simulador:
    def __init__(self, processos, cpus=1, sched="fcfs", quantum=None, subst="lru",
                 quadros=256, custo_troca=1, fault=20, log=None, gantt_fim=200,
                 log_max=20, log_ate=None):
        if sched == "rr" and (quantum is None or quantum < 1):
            raise ValueError("RR exige --quantum >= 1")
        self.processos = sorted((Processo(d) for d in processos), key=lambda p: (p.chegada, p.pid))
        self.por_pid = {p.pid: p for p in self.processos}
        if len(self.por_pid) != len(self.processos):
            raise ValueError("a carga tem pids repetidos")
        self.n = len(self.processos)
        self.ncpus = cpus
        self.sched = sched
        self.quantum = quantum
        self.custo_troca = custo_troca
        self.fault_tempo = fault
        self.memoria = Memoria(quadros, subst)
        self.fila = FilaProntos(sched)
        self.gantt_fim = gantt_fim
        self.log = log              # arquivo aberto ou None
        self.log_max = log_max      # quantos pids listar por conjunto em cada linha do log (None = todos)
        self.log_ate = log_ate      # último instante registrado no log (None = até o fim)

        self.cpu_pid = [None] * cpus
        self.cpu_estado = [CPU_LIVRE] * cpus
        self.cpu_fim_troca = [0] * cpus
        self.cpu_ultimo = [None] * cpus
        self.util = [0] * cpus
        self.troca = [0] * cpus
        self.ocioso = [0] * cpus
        self.trocas = [0] * cpus
        self.gantt = [[] for _ in range(cpus)]

        self.bloqueados = []   # heap (retorno, pid)
        self.prox_chegada = 0
        self.terminados = 0
        self.faults = 0
        self.refs_acerto = 0
        self.refaults_sem_vitima = 0
        self.makespan = 0
        self.t = 0

    # ------------------------------------------------------------------ log
    def _log(self, t, evento, pid=None, extra=""):
        if self.log is None or (self.log_ate is not None and t > self.log_ate):
            return
        n = self.log_max
        # fila de prontos em ordem de despacho e bloqueados em ordem de retorno (o heap já tem (retorno, pid));
        # listas longas são truncadas em n itens, com o total entre parênteses
        prontos = self.fila.pids(n)
        bl = heapq.nsmallest(n, self.bloqueados) if n else sorted(self.bloqueados)
        resto_p = f" ... ({len(self.fila)} no total)" if len(self.fila) > len(prontos) else ""
        resto_b = f" ... ({len(self.bloqueados)} no total)" if len(self.bloqueados) > len(bl) else ""
        cpus = ["-" if self.cpu_pid[c] is None else
                ("P%d%s" % (self.cpu_pid[c].pid, "(troca)" if self.cpu_estado[c] == CPU_TROCA else ""))
                for c in range(self.ncpus)]
        self.log.write(
            f"t={t} | {evento}{'' if pid is None else ' pid=' + str(pid)}{(' ' + extra) if extra else ''}"
            f" | prontos={prontos}{resto_p}"
            f" | bloqueados={{{', '.join(f'{p}:{r}' for r, p in bl)}}}{resto_b}"
            f" | cpus={cpus}"
            f" | quadros={self.memoria.ocupados()}/{self.memoria.quadros}\n")

    def _gantt(self, c, ini, fim, evento):
        if ini >= self.gantt_fim:
            return
        fim = min(fim, self.gantt_fim)
        g = self.gantt[c]
        # intervalos iguais e contíguos são unidos, exceto TROCA: cada troca é uma linha própria no CSV,
        # como no exemplo da seção 9.2, para que o número de linhas TROCA seja o número de trocas
        if evento != "TROCA" and g and g[-1][2] == evento and g[-1][1] == ini:
            g[-1][1] = fim
        else:
            g.append([ini, fim, evento])

    # ----------------------------------------------------------- bloqueio
    def _bloquear(self, p, ate, motivo):
        ate = max(ate, self.t + 1)   # o retorno é sempre um instante futuro (só importa com --fault 0)
        p.estado = BLOQUEADO
        p.motivo = motivo
        p.retorno = ate
        heapq.heappush(self.bloqueados, (ate, p.pid))

    def _liberar_cpu(self, c):
        self.cpu_ultimo[c] = self.cpu_pid[c].pid
        self.cpu_pid[c] = None
        self.cpu_estado[c] = CPU_LIVRE

    # --------------------------------------------------- um instante t
    def _tick(self):
        t = self.t
        # 1. chegadas e retornos (E/S e page fault) entram na fila em ordem de pid
        entrantes = []
        while self.prox_chegada < self.n and self.processos[self.prox_chegada].chegada == t:
            p = self.processos[self.prox_chegada]
            self.prox_chegada += 1
            entrantes.append((p, "CHEGADA"))
        while self.bloqueados and self.bloqueados[0][0] == t:
            _, pid = heapq.heappop(self.bloqueados)
            p = self.por_pid[pid]
            if p.fault_pendente is not None:
                # "sem vítima": tenta de novo trazer a página. Se conseguir, espera o tempo do fault
                # (transferência); se não, espera mais 20 e tenta de novo. Não conta novo fault.
                chave = p.fault_pendente
                if self.memoria.carregar(chave, t):
                    p.fault_pendente = None
                    self._bloquear(p, t + self.fault_tempo, "fault")
                    self._log(t, "FAULT_RETENTATIVA_OK", p.pid, f"pag={chave[1]} retorno={p.retorno}")
                else:
                    self.refaults_sem_vitima += 1
                    self._bloquear(p, t + self.fault_tempo, "fault")
                    self._log(t, "FAULT_SEM_VITIMA", p.pid, f"pag={chave[1]} retorno={p.retorno}")
                continue
            entrantes.append((p, "RETORNO_FAULT" if p.motivo == "fault" else "RETORNO_ES"))
        entrantes.sort(key=lambda x: x[0].pid)
        for p, ev in entrantes:
            self.fila.inserir(p, t)
            self._log(t, ev, p.pid)

        # 2. fim de troca de contexto e fim de quantum (RR)
        for c in range(self.ncpus):
            if self.cpu_estado[c] == CPU_TROCA and self.cpu_fim_troca[c] == t:
                self.cpu_estado[c] = CPU_EXEC
            if self.cpu_estado[c] == CPU_EXEC and self.sched == "rr":
                p = self.cpu_pid[c]
                if p.quantum_restante == 0:
                    if len(self.fila) > 0:
                        p.preempcoes += 1
                        self._liberar_cpu(c)
                        self.fila.inserir(p, t)        # vai para o fim da fila
                        self._log(t, "PREEMPCAO_QUANTUM", p.pid, f"cpu={c}")
                    else:
                        p.quantum_restante = self.quantum   # continua sem custo

        # 3a. verificação de referência dos processos que já estavam executando
        for c in range(self.ncpus):
            if self.cpu_estado[c] == CPU_EXEC:
                self._verificar_referencia(c, t)

        # 3b. despachos (CPU de menor índice primeiro); um fault no despacho libera a CPU de novo
        for c in range(self.ncpus):
            while self.cpu_estado[c] == CPU_LIVRE and len(self.fila) > 0:
                p = self.fila.retirar()
                p.espera += t - p.entrada_fila
                if p.primeiro_despacho is None:
                    p.primeiro_despacho = t
                p.estado = EXECUTANDO
                p.quantum_restante = self.quantum if self.sched == "rr" else 0
                self.cpu_pid[c] = p
                if self.cpu_ultimo[c] != p.pid:
                    self.trocas[c] += 1
                    if self.custo_troca > 0:
                        self.cpu_estado[c] = CPU_TROCA
                        self.cpu_fim_troca[c] = t + self.custo_troca
                        self._gantt(c, t, t + self.custo_troca, "TROCA")
                        self._log(t, "DESPACHO", p.pid, f"cpu={c} troca_ate={t + self.custo_troca}")
                        break
                self.cpu_estado[c] = CPU_EXEC
                self._log(t, "DESPACHO", p.pid, f"cpu={c}")
                self._verificar_referencia(c, t)

        # 4. avança o tempo de t para t+1
        for c in range(self.ncpus):
            est = self.cpu_estado[c]
            if est == CPU_EXEC:
                p = self.cpu_pid[c]
                self.util[c] += 1
                self.refs_acerto += 1
                p.pos += 1
                if self.sched == "rr":
                    p.quantum_restante -= 1
                self._gantt(c, t, t + 1, f"P{p.pid}")
                if p.pos == p.burst()["cpu"]:
                    self._fim_burst(c, p, t + 1)
            elif est == CPU_TROCA:
                self.troca[c] += 1          # o intervalo de TROCA já foi registrado no despacho
            else:
                self.ocioso[c] += 1
                self._gantt(c, t, t + 1, "OCIOSA")

    def _verificar_referencia(self, c, t):
        p = self.cpu_pid[c]
        chave = (p.pid, p.pagina_atual())
        if self.memoria.presente(chave):
            self.memoria.acerto(chave)
            return
        # page fault: processo bloqueia, CPU fica livre, unidade não consumida
        self.faults += 1
        p.faults += 1
        if self.fault_tempo == 0 and self.memoria.carregar(chave, t):
            # fault de custo zero: a página entra na hora e a execução segue (útil para os exemplos "sem memória")
            self.memoria.acerto(chave)
            self._log(t, "FAULT_INSTANTANEO", p.pid, f"cpu={c} pag={chave[1]}")
            return
        self._liberar_cpu(c)
        if self.memoria.carregar(chave, t):
            self._bloquear(p, t + self.fault_tempo, "fault")
            self._log(t, "FAULT", p.pid, f"cpu={c} pag={chave[1]} retorno={p.retorno}")
        else:
            p.fault_pendente = chave
            self._bloquear(p, t + self.fault_tempo, "fault")
            self._log(t, "FAULT_SEM_VITIMA", p.pid, f"cpu={c} pag={chave[1]} retorno={p.retorno}")

    def _fim_burst(self, c, p, t1):
        self._liberar_cpu(c)
        p.idx += 1
        p.pos = 0
        if p.idx == len(p.bursts):
            p.estado = TERMINADO
            p.termino = t1
            self.terminados += 1
            self.memoria.liberar(p.pid)
            self._log(t1, "TERMINO", p.pid, f"cpu={c}")
        else:
            es = p.bursts[p.idx]["es"]
            p.idx += 1  # próximo burst de CPU
            self._bloquear(p, t1 + es, "es")
            self._log(t1, "FIM_BURST_ES", p.pid, f"cpu={c} es={es} retorno={p.retorno}")

    # ------------------------------------------------------------ execução
    def executar(self):
        t = 0
        while self.terminados < self.n:
            # Atalho: nada pronto e nenhuma CPU ocupada -> salta até o próximo evento
            if len(self.fila) == 0 and all(e == CPU_LIVRE for e in self.cpu_estado):
                prox = []
                if self.prox_chegada < self.n:
                    prox.append(self.processos[self.prox_chegada].chegada)
                if self.bloqueados:
                    prox.append(self.bloqueados[0][0])
                alvo = min(prox)
                if alvo > t:
                    for c in range(self.ncpus):
                        self.ocioso[c] += alvo - t
                        self._gantt(c, t, alvo, "OCIOSA")
                    t = alvo
            self.t = t
            self._tick()
            t += 1
        self.makespan = t
        self.t = t
        return self.metricas()

    # ------------------------------------------------------------- métricas
    def metricas(self) -> dict:
        ps = self.processos
        n = self.n
        esperas = [p.espera for p in ps]
        turn = [p.termino - p.chegada for p in ps]
        resp = [p.primeiro_despacho - p.chegada for p in ps]
        total_refs = self.refs_acerto + self.faults
        mk = self.makespan
        por_cpu = []
        for c in range(self.ncpus):
            por_cpu.append({
                "cpu": c, "util": self.util[c], "troca": self.troca[c], "ocioso": self.ocioso[c],
                "util_pct": 100.0 * self.util[c] / mk, "troca_pct": 100.0 * self.troca[c] / mk,
                "ocioso_pct": 100.0 * self.ocioso[c] / mk,
                "utilizacao": (self.util[c] + self.troca[c]) / mk, "trocas": self.trocas[c],
            })
        por_classe = {}
        for cl in sorted({p.classe for p in ps}):
            sub = [p for p in ps if p.classe == cl]
            por_classe[cl] = {
                "n": len(sub),
                "espera_media": sum(p.espera for p in sub) / len(sub),
                "espera_max": max(p.espera for p in sub),
                "turnaround_medio": sum(p.termino - p.chegada for p in sub) / len(sub),
                "resposta_media": sum(p.primeiro_despacho - p.chegada for p in sub) / len(sub),
                "cpu_total": sum(p.cpu_total for p in sub),
                "faults": sum(p.faults for p in sub),
            }
        pior = max(ps, key=lambda p: p.espera)
        return {
            "n_processos": n,
            "espera_media": sum(esperas) / n,
            "espera_max": max(esperas),
            "espera_max_pid": pior.pid,
            "turnaround_medio": sum(turn) / n,
            "turnaround_max": max(turn),
            "resposta_media": sum(resp) / n,
            "resposta_max": max(resp),
            "makespan": mk,
            "trocas_total": sum(self.trocas),
            "preempcoes_total": sum(p.preempcoes for p in ps),
            "utilizacao_media": sum((self.util[c] + self.troca[c]) for c in range(self.ncpus)) / (mk * self.ncpus),
            "util_total": sum(self.util), "troca_total": sum(self.troca), "ocioso_total": sum(self.ocioso),
            "page_faults": self.faults,
            "referencias": total_refs,
            "taxa_faults": self.faults / total_refs if total_refs else 0.0,
            "substituicoes": self.memoria.substituicoes,
            "refaults_sem_vitima": self.refaults_sem_vitima,
            "cpu_total_carga": sum(p.cpu_total for p in ps),
            "es_total_carga": sum(p.es_total for p in ps),
            "fila_max": self.fila.tamanho_max,
            "por_cpu": por_cpu,
            "por_classe": por_classe,
        }

    def tabela_processos(self):
        return [{"pid": p.pid, "classe": p.classe, "chegada": p.chegada, "cpu": p.cpu_total,
                 "espera": p.espera, "resposta": p.primeiro_despacho - p.chegada,
                 "turnaround": p.termino - p.chegada, "faults": p.faults, "preempcoes": p.preempcoes}
                for p in self.processos]

    def escrever_gantt(self, caminho):
        with open(caminho, "w") as f:
            f.write("cpu,inicio,fim,evento\n")
            for c in range(self.ncpus):
                for ini, fim, ev in self.gantt[c]:
                    f.write(f"{c},{ini},{fim},{ev}\n")


# ----------------------------------------------------------------------------
# Saída
# ----------------------------------------------------------------------------
def carregar_processos(caminho):
    with open(caminho) as f:
        dados = json.load(f)
    if isinstance(dados, dict):
        for chave in ("processos", "procs", "carga"):
            if chave in dados:
                dados = dados[chave]
                break
    if not isinstance(dados, list):
        raise ValueError("JSON deve ser uma lista de processos (ou um objeto com a chave 'processos')")
    return dados


def nome_config(args):
    base = os.path.splitext(os.path.basename(args.arquivo))[0]
    q = f"_q{args.quantum}" if args.sched == "rr" else ""
    fault = f"_fault{args.fault}" if args.fault != 20 else ""
    return f"{base}_{args.sched}{q}_cpus{args.cpus}_{args.subst}_{args.quadros}quadros_troca{args.custo_troca}{fault}"


def imprimir_resumo(args, m, saida=sys.stdout):
    w = saida.write
    w("=" * 78 + "\n")
    w("RESUMO DA EXECUÇÃO\n")
    w("=" * 78 + "\n")
    w("Parâmetros:\n")
    w(f"  arquivo={args.arquivo}  cpus={args.cpus}  sched={args.sched}"
      f"{'  quantum=' + str(args.quantum) if args.sched == 'rr' else ''}"
      f"  subst={args.subst}  quadros={args.quadros}  custo_troca={args.custo_troca}  fault={args.fault}\n")
    w(f"  processos={m['n_processos']}  cpu_total_carga={m['cpu_total_carga']}  es_total_carga={m['es_total_carga']}\n")
    w("-" * 78 + "\n")
    w("Métricas globais:\n")
    w(f"  Espera média        : {m['espera_media']:.2f}\n")
    w(f"  Espera máxima       : {m['espera_max']} (pid {m['espera_max_pid']})\n")
    w(f"  Turnaround médio    : {m['turnaround_medio']:.2f}\n")
    w(f"  Resposta média      : {m['resposta_media']:.2f}\n")
    w(f"  Makespan            : {m['makespan']}\n")
    w(f"  Trocas de contexto  : {m['trocas_total']} (preempções por quantum: {m['preempcoes_total']})\n")
    w(f"  Utilização média    : {100 * m['utilizacao_media']:.2f}%\n")
    w(f"  Tamanho máx. da fila: {m['fila_max']}\n")
    for cl, d in m["por_classe"].items():
        w(f"  Classe {cl:<6}: n={d['n']}  espera média={d['espera_media']:.2f}  espera máx={d['espera_max']}"
          f"  turnaround médio={d['turnaround_medio']:.2f}  resposta média={d['resposta_media']:.2f}\n")
    w("-" * 78 + "\n")
    w("Por CPU:\n")
    w(f"  {'CPU':>3} {'útil':>8} {'troca':>8} {'ocioso':>8} {'útil%':>8} {'troca%':>8} {'ocioso%':>8} {'utiliz.':>8} {'trocas':>7}\n")
    for d in m["por_cpu"]:
        w(f"  {d['cpu']:>3} {d['util']:>8} {d['troca']:>8} {d['ocioso']:>8} {d['util_pct']:>7.2f}% {d['troca_pct']:>7.2f}%"
          f" {d['ocioso_pct']:>7.2f}% {100 * d['utilizacao']:>7.2f}% {d['trocas']:>7}\n")
    w("-" * 78 + "\n")
    w("Memória:\n")
    w(f"  Page faults totais  : {m['page_faults']}\n")
    w(f"  Referências totais  : {m['referencias']} (acertos + faults)\n")
    w(f"  Taxa de faults      : {100 * m['taxa_faults']:.2f}% (faults / referências)\n")
    w(f"  Substituições       : {m['substituicoes']}   Retentativas sem vítima: {m['refaults_sem_vitima']}\n")
    w("=" * 78 + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Simulador de escalonamento com paginação sob demanda")
    ap.add_argument("--arquivo", required=True, help="caminho do JSON da carga")
    ap.add_argument("--cpus", type=int, default=1)
    ap.add_argument("--sched", choices=["fcfs", "sjf", "rr"], default="fcfs")
    ap.add_argument("--quantum", type=int, default=None, help="quantum (só no RR)")
    ap.add_argument("--subst", choices=["fifo", "lru"], default="lru")
    ap.add_argument("--quadros", type=int, default=256)
    ap.add_argument("--custo-troca", type=int, default=1, dest="custo_troca")
    ap.add_argument("--fault", type=int, default=20, help="duração do page fault (padrão 20)")
    ap.add_argument("--log", nargs="?", const="", default=None, metavar="ARQ",
                    help="gera o log de estados (opcionalmente no arquivo ARQ)")
    ap.add_argument("--log-max", type=int, default=20, dest="log_max", metavar="N",
                    help="quantos pids listar por conjunto em cada linha do log; 0 = todos (padrão 20)")
    ap.add_argument("--log-ate", type=int, default=None, dest="log_ate", metavar="T",
                    help="registra o log só até o instante T (padrão: até o fim)")
    ap.add_argument("--gantt", default=None, metavar="ARQ", help="CSV do Gantt (padrão: gantt_<config>.csv)")
    ap.add_argument("--gantt-fim", type=int, default=200, dest="gantt_fim", help="fim da janela do Gantt (padrão 200)")
    ap.add_argument("--json", default=None, metavar="ARQ", help="grava as métricas em JSON")
    ap.add_argument("--processos", default=None, metavar="ARQ", help="grava CSV com métricas por processo")
    ap.add_argument("--saida-dir", default=".", dest="saida_dir", help="diretório dos arquivos gerados")
    ap.add_argument("--quieto", action="store_true", help="não imprime o resumo")
    args = ap.parse_args(argv)

    if args.sched == "rr" and args.quantum is None:
        ap.error("--quantum é obrigatório com --sched rr")
    if args.cpus < 1:
        ap.error("--cpus deve ser >= 1")

    os.makedirs(args.saida_dir, exist_ok=True)
    nome = nome_config(args)
    dados = carregar_processos(args.arquivo)

    log_f = None
    if args.log is not None:
        caminho_log = args.log or os.path.join(args.saida_dir, f"log_{nome}.txt")
        log_f = open(caminho_log, "w")

    sim = Simulador(dados, cpus=args.cpus, sched=args.sched, quantum=args.quantum, subst=args.subst,
                    quadros=args.quadros, custo_troca=args.custo_troca, fault=args.fault,
                    log=log_f, gantt_fim=args.gantt_fim,
                    log_max=args.log_max or None, log_ate=args.log_ate)
    try:
        m = sim.executar()
    finally:
        if log_f:
            log_f.close()

    gantt_path = args.gantt or os.path.join(args.saida_dir, f"gantt_{nome}.csv")
    sim.escrever_gantt(gantt_path)

    m["parametros"] = {k: getattr(args, k) for k in
                       ("arquivo", "cpus", "sched", "quantum", "subst", "quadros", "custo_troca", "fault")}
    m["config"] = nome
    if args.json:
        with open(args.json, "w") as f:
            json.dump(m, f, indent=2, ensure_ascii=False)
    if args.processos:
        with open(args.processos, "w", newline="") as f:
            linhas = sim.tabela_processos()
            wr = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
            wr.writeheader()
            wr.writerows(linhas)
    if not args.quieto:
        imprimir_resumo(args, m)
        print(f"Gantt (0..{args.gantt_fim}) gravado em: {gantt_path}")
        if log_f:
            print(f"Log de estados gravado em: {caminho_log}")
    return m


if __name__ == "__main__":
    main()
