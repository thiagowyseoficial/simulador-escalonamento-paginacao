"""
Testes com os exemplos do enunciado (seções 5.4, 5.5, 7.1, 7.2 e 7.3).
Rodar: .venv/bin/python -m pytest -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from simulador import Simulador  # noqa: E402


def proc(pid, chegada, cpu, refs=None, paginas=None, extra=None):
    refs = refs if refs is not None else [0] * cpu
    paginas = paginas or (max(refs) + 1)
    bursts = [{"cpu": cpu, "refs": refs}] + (extra or [])
    return {"pid": pid, "chegada": chegada, "classe": "curto", "paginas": paginas, "bursts": bursts}


def rodar(procs, **kw):
    kw.setdefault("quadros", 1000)
    sim = Simulador(procs, **kw)
    m = sim.executar()
    return sim, m, {p.pid: p for p in sim.processos}


P17 = {"pid": 17, "chegada": 342, "classe": "curto", "paginas": 6, "bursts": [
    {"cpu": 4, "refs": [0, 1, 1, 2]}, {"es": 12}, {"cpu": 3, "refs": [2, 3, 3]}]}


def test_5_4_processo_17_sem_faults():
    # fault=0: página carregada na hora, equivale ao exemplo sem page faults
    sim, m, ps = rodar([P17], cpus=1, sched="fcfs", custo_troca=0, fault=0)
    # turnaround = 7 (CPU) + 12 (E/S) = 19
    p = ps[17]
    assert p.termino - p.chegada == 19
    assert p.espera == 0


def test_5_5_processo_17_com_faults():
    sim, m, ps = rodar([P17], cpus=1, sched="fcfs", custo_troca=0, fault=20)
    p = ps[17]
    assert p.faults == 4
    assert p.termino == 441
    assert p.termino - p.chegada == 99
    assert p.espera == 0
    assert m["makespan"] == 441


def ex_7_1():
    return [proc(1, 0, 5), proc(2, 0, 3), proc(3, 2, 2)]


def test_7_1_fcfs():
    sim, m, ps = rodar(ex_7_1(), cpus=1, sched="fcfs", custo_troca=1, fault=0)
    assert [ps[i].espera for i in (1, 2, 3)] == [0, 6, 8]
    assert [ps[i].primeiro_despacho - ps[i].chegada for i in (1, 2, 3)] == [0, 6, 8]
    assert [ps[i].termino - ps[i].chegada for i in (1, 2, 3)] == [6, 10, 11]
    assert m["makespan"] == 13
    cpu = m["por_cpu"][0]
    assert (cpu["util"], cpu["troca"], cpu["ocioso"]) == (10, 3, 0)
    assert m["trocas_total"] == 3
    assert abs(m["espera_media"] - 4.67) < 0.01 and m["espera_max"] == 8
    assert abs(m["turnaround_medio"] - 9) < 1e-9
    assert sim.gantt[0] == [[0, 1, "TROCA"], [1, 6, "P1"], [6, 7, "TROCA"], [7, 10, "P2"], [10, 11, "TROCA"], [11, 13, "P3"]]


def test_7_1_sjf():
    # t=0: fila [P1(5), P2(3)] -> P2. 0-1 troca, 1-4 P2. t=4: fila [P1, P3(2)] -> P3: 4-5 troca, 5-7. P1: 7-8 troca, 8-13.
    sim, m, ps = rodar(ex_7_1(), cpus=1, sched="sjf", custo_troca=1, fault=0)
    assert [ps[i].espera for i in (1, 2, 3)] == [7, 0, 2]
    assert [ps[i].termino for i in (1, 2, 3)] == [13, 4, 7]
    assert m["makespan"] == 13


def test_7_1_rr_q2():
    # feito à mão: P1 1-3 | P2 4-6 | P3 7-9 | P1 10-12 | P2 13-14 | P1 15-16 (trocas de 1 antes de cada)
    sim, m, ps = rodar(ex_7_1(), cpus=1, sched="rr", quantum=2, custo_troca=1, fault=0)
    assert m["makespan"] == 16
    assert [ps[i].espera for i in (1, 2, 3)] == [8, 9, 4]
    assert [ps[i].termino for i in (1, 2, 3)] == [16, 14, 9]
    assert m["trocas_total"] == 6
    assert sim.gantt[0] == [[0, 1, "TROCA"], [1, 3, "P1"], [3, 4, "TROCA"], [4, 6, "P2"], [6, 7, "TROCA"],
                            [7, 9, "P3"], [9, 10, "TROCA"], [10, 12, "P1"], [12, 13, "TROCA"], [13, 14, "P2"],
                            [14, 15, "TROCA"], [15, 16, "P1"]]


def test_rr_fila_vazia_continua_sem_custo():
    sim, m, ps = rodar([proc(1, 0, 7)], cpus=1, sched="rr", quantum=2, custo_troca=1, fault=0)
    assert m["makespan"] == 8
    assert m["trocas_total"] == 1
    assert ps[1].preempcoes == 0


def test_7_2_faults_e_fila():
    procs = [proc(1, 0, 2, [0, 1]), proc(2, 1, 2, [0, 1]), proc(3, 2, 1, [0])]
    sim, m, ps = rodar(procs, cpus=1, sched="fcfs", custo_troca=0, fault=5)
    assert [ps[i].espera for i in (1, 2, 3)] == [0, 0, 0]
    assert [ps[i].termino - ps[i].chegada for i in (1, 2, 3)] == [12, 12, 6]
    assert m["page_faults"] == 5
    assert m["makespan"] == 13
    assert m["por_cpu"][0]["util"] == 5
    assert m["por_cpu"][0]["ocioso"] == 8


def ex_7_3():
    return [proc(1, 0, 4, [0] * 4), proc(2, 0, 3, [0] * 3), proc(3, 0, 2, [0] * 2)]


def test_7_3_fcfs():
    sim, m, ps = rodar(ex_7_3(), cpus=1, sched="fcfs", custo_troca=0, fault=20)
    assert [ps[i].espera for i in (1, 2, 3)] == [0, 4, 7]
    assert abs(m["espera_media"] - 3.67) < 0.01
    assert [ps[i].termino for i in (1, 2, 3)] == [24, 27, 29]


def test_7_3_sjf():
    sim, m, ps = rodar(ex_7_3(), cpus=1, sched="sjf", custo_troca=0, fault=20)
    assert [ps[i].espera for i in (1, 2, 3)] == [5, 2, 0]
    assert abs(m["espera_media"] - 2.33) < 0.01
    assert [ps[i].termino for i in (1, 2, 3)] == [29, 25, 22]


def test_volta_de_es_mesma_cpu_sem_custo():
    # P1: cpu 2, es 3, cpu 2. Sozinho: troca 0-1, exec 1-3, es 3-6, exec 6-8 (sem nova troca)
    p = proc(1, 0, 2, [0, 0], extra=[{"es": 3}, {"cpu": 2, "refs": [0, 0]}])
    sim, m, ps = rodar([p], cpus=1, sched="fcfs", custo_troca=1, fault=0)
    assert m["trocas_total"] == 1
    assert m["makespan"] == 8
    assert m["por_cpu"][0]["ocioso"] == 3


def test_4_cpus_menor_indice_primeiro():
    procs = [proc(i, 0, 3) for i in (1, 2, 3, 4, 5)]
    sim, m, ps = rodar(procs, cpus=4, sched="fcfs", custo_troca=1, fault=0)
    assert [sim.gantt[c][1][2] for c in range(4)] == ["P1", "P2", "P3", "P4"]
    assert ps[5].primeiro_despacho == 4
    assert m["makespan"] == 8
    assert sum(d["util"] + d["troca"] + d["ocioso"] for d in m["por_cpu"]) == 4 * m["makespan"]


def test_fifo_vs_lru_substituicao():
    # 2 quadros. Refs: 0,1,0,2 -> FIFO retira 0 (mais antiga carga); LRU retira 1 (menos usada)
    p = proc(1, 0, 4, [0, 1, 0, 2], paginas=3)
    _, m_fifo, _ = rodar([p], cpus=1, sched="fcfs", custo_troca=0, fault=1, quadros=2, subst="fifo")
    _, m_lru, _ = rodar([p], cpus=1, sched="fcfs", custo_troca=0, fault=1, quadros=2, subst="lru")
    assert m_fifo["page_faults"] == 3 and m_lru["page_faults"] == 3
    # agora refs 0,1,0,2,0: FIFO dá fault na última (0 foi vítima), LRU não
    p = proc(1, 0, 5, [0, 1, 0, 2, 0], paginas=3)
    _, m_fifo, _ = rodar([p], cpus=1, sched="fcfs", custo_troca=0, fault=1, quadros=2, subst="fifo")
    _, m_lru, _ = rodar([p], cpus=1, sched="fcfs", custo_troca=0, fault=1, quadros=2, subst="lru")
    assert m_fifo["page_faults"] == 4
    assert m_lru["page_faults"] == 3


def test_pagina_protegida_nao_e_vitima():
    # 1 quadro, 2 processos. P1 fault carrega (1,0) protegida. P2 fault: sem vítima -> retenta.
    procs = [proc(1, 0, 1, [0]), proc(2, 0, 1, [0])]
    sim, m, ps = rodar(procs, cpus=2, sched="fcfs", custo_troca=0, fault=5, quadros=1)
    assert m["page_faults"] == 2
    # P2 dá fault em t=0 sem vítima (o único quadro está protegido para P1), retenta em t=5 (ainda
    # protegido: P1 só reexecuta em 5-6), retenta em t=10, consegue o quadro e espera mais 5 (interpretação
    # documentada no README): executa em 15-16.
    assert m["refaults_sem_vitima"] == 1
    assert ps[1].termino == 6 and ps[2].termino == 16
    assert m["makespan"] == 16
    assert sim.gantt[0] == [[0, 5, "OCIOSA"], [5, 6, "P1"], [6, 15, "OCIOSA"], [15, 16, "P2"]]
    assert sim.memoria.ocupados() == 0  # tudo liberado no término


def test_termino_libera_quadros():
    procs = [proc(1, 0, 2, [0, 1], paginas=2), proc(2, 0, 2, [0, 1], paginas=2)]
    sim, m, ps = rodar(procs, cpus=1, sched="fcfs", custo_troca=0, fault=1, quadros=4)
    assert sim.memoria.ocupados() == 0
    assert m["page_faults"] == 4


def test_carga_invalida_da_erro_claro():
    import pytest
    with pytest.raises(ValueError, match="cpu=0"):
        rodar([{"pid": 1, "chegada": 0, "classe": "curto", "paginas": 1, "bursts": [{"cpu": 0, "refs": []}]}])
    with pytest.raises(ValueError, match="alternam"):
        rodar([proc(1, 0, 1, extra=[{"cpu": 1, "refs": [0]}])])
    with pytest.raises(ValueError, match="repetidos"):
        rodar([proc(1, 0, 1), proc(1, 0, 1)])
    with pytest.raises(ValueError, match="negativa"):
        rodar([proc(1, -1, 1)])


def test_sjf_usa_parte_restante_apos_fault():
    # P1: burst 5, refs [0,0,1,1,1]. fault(0) 0-20; executa 20-22; fault(1) em 22, volta em 42 com restante 3.
    # P2 (burst 4) chega em 42, junto com o retorno de P1. SJF compara 3 (parte que falta) com 4 -> P1 primeiro.
    # Se usasse o burst inteiro (5), escolheria P2.
    p1 = proc(1, 0, 5, [0, 0, 1, 1, 1], paginas=2)
    p2 = proc(2, 42, 4)
    sim, m, ps = rodar([p1, p2], cpus=1, sched="sjf", custo_troca=0, fault=20)
    assert ps[1].termino == 45 and ps[2].termino == 69   # P2: espera 3, fault 45-65, executa 65-69
    assert ps[1].espera == 0 and ps[2].espera == 3
