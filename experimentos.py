#!/usr/bin/env python3
"""
Roda o desenho experimental da seção 11 e monta as tabelas resumo.

  python experimentos.py --cargas cargas/cenario_A_t0.json cargas/cenario_B_espacadas.json \
                         --quantums 2 10 50 --saida resultados

Para cada carga: FCFS, SJF e RR (um por quantum) x {1, 4} CPUs, com LRU e
--quadros 256 (execuções base). Depois, comparação FIFO x LRU nas
configurações listadas em --fifo-lru (padrão: fcfs e rr com o 2º quantum,
em 1 e 4 CPUs) e, opcionalmente, variação de quadros (--quadros-extra).

Saídas (em --saida):
  <carga>/<config>.json         métricas completas de cada execução
  <carga>/gantt_<config>.csv    Gantt 0..200 de cada execução
  tabela_<carga>.csv / .md      uma linha por execução, uma coluna por métrica obrigatória
  fifo_lru_<carga>.csv / .md    comparação FIFO x LRU
  todas.json                    lista com todas as métricas (usada por graficos.py)
"""
import argparse
import csv
import json
import os
import time
from multiprocessing import Pool

from simulador import Simulador, carregar_processos


def nome_config(sched, quantum, cpus, subst, quadros, custo):
    q = f"_q{quantum}" if sched == "rr" else ""
    return f"{sched}{q}_cpus{cpus}_{subst}_{quadros}quadros_troca{custo}"


def rodar_um(job):
    carga, caminho, sched, quantum, cpus, subst, quadros, custo, fault, saida = job
    nome = nome_config(sched, quantum, cpus, subst, quadros, custo)
    dados = carregar_processos(caminho)
    ini = time.time()
    sim = Simulador(dados, cpus=cpus, sched=sched, quantum=quantum, subst=subst,
                    quadros=quadros, custo_troca=custo, fault=fault)
    m = sim.executar()
    m["tempo_execucao_s"] = round(time.time() - ini, 2)
    m["parametros"] = dict(arquivo=caminho, cpus=cpus, sched=sched, quantum=quantum, subst=subst,
                           quadros=quadros, custo_troca=custo, fault=fault)
    m["carga"] = carga
    m["config"] = nome
    m["rotulo"] = sched.upper() + (f" q={quantum}" if sched == "rr" else "")
    d = os.path.join(saida, carga)
    os.makedirs(d, exist_ok=True)
    sim.escrever_gantt(os.path.join(d, f"gantt_{nome}.csv"))
    with open(os.path.join(d, f"{nome}.json"), "w") as f:
        json.dump(m, f, indent=1, ensure_ascii=False)
    print(f"[{carga}] {nome:<40} makespan={m['makespan']:>8} espera={m['espera_media']:>10.2f} "
          f"faults={m['page_faults']:>7}  ({m['tempo_execucao_s']}s)", flush=True)
    return m


COLS = [
    ("rotulo", "Escalonador"), ("cpus", "CPUs"), ("subst", "Subst."), ("quadros", "Quadros"),
    ("espera_media", "Espera média"), ("espera_max", "Espera máx."), ("turnaround_medio", "Turnaround médio"),
    ("resposta_media", "Resposta média"), ("makespan", "Makespan"), ("speedup", "Speedup"),
    ("eficiencia", "Eficiência"), ("trocas_total", "Trocas ctx"), ("utilizacao_pct", "Utilização %"),
    ("ocioso_total", "Ocioso total"), ("page_faults", "Page faults"), ("taxa_faults_pct", "Taxa faults %"),
]


def linha(m, mk1=None):
    p = m["parametros"]
    sp = (mk1 / m["makespan"]) if (mk1 and p["cpus"] > 1) else None
    return {
        "rotulo": m["rotulo"], "cpus": p["cpus"], "subst": p["subst"], "quadros": p["quadros"],
        "espera_media": round(m["espera_media"], 2), "espera_max": m["espera_max"],
        "turnaround_medio": round(m["turnaround_medio"], 2), "resposta_media": round(m["resposta_media"], 2),
        "makespan": m["makespan"],
        "speedup": round(sp, 3) if sp else "", "eficiencia": round(sp / p["cpus"], 3) if sp else "",
        "trocas_total": m["trocas_total"], "utilizacao_pct": round(100 * m["utilizacao_media"], 2),
        "ocioso_total": m["ocioso_total"], "page_faults": m["page_faults"],
        "taxa_faults_pct": round(100 * m["taxa_faults"], 2),
    }


def escrever_tabela(linhas, caminho_base, titulo):
    with open(caminho_base + ".csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[c for c, _ in COLS])
        w.writeheader()
        w.writerows(linhas)
    with open(caminho_base + ".md", "w") as f:
        f.write(f"## {titulo}\n\n")
        f.write("| " + " | ".join(h for _, h in COLS) + " |\n")
        f.write("|" + "|".join("---" for _ in COLS) + "|\n")
        for l in linhas:
            f.write("| " + " | ".join(str(l[c]) for c, _ in COLS) + " |\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cargas", nargs="+", required=True)
    ap.add_argument("--quantums", nargs="+", type=int, default=[2, 10, 50])
    ap.add_argument("--quadros", type=int, default=256)
    ap.add_argument("--custo-troca", type=int, default=1, dest="custo_troca")
    ap.add_argument("--fault", type=int, default=20)
    ap.add_argument("--fifo-lru", nargs="*", default=None, dest="fifo_lru",
                    help="configs para comparar FIFO x LRU, formato sched[:quantum]/cpus (ex.: fcfs/1 rr:10/4)")
    ap.add_argument("--quadros-extra", nargs="*", type=int, default=[], dest="quadros_extra",
                    help="valores extras de quadros para variar (com LRU, fcfs e rr)")
    ap.add_argument("--saida", default="resultados")
    ap.add_argument("--paralelo", type=int, default=os.cpu_count())
    args = ap.parse_args()

    os.makedirs(args.saida, exist_ok=True)
    cargas = [(os.path.splitext(os.path.basename(c))[0], c) for c in args.cargas]
    q2 = args.quantums[min(1, len(args.quantums) - 1)]
    fifo_lru = args.fifo_lru if args.fifo_lru is not None else ["fcfs/1", "fcfs/4", f"rr:{q2}/1", f"rr:{q2}/4"]

    jobs = []
    for carga, caminho in cargas:
        for cpus in (1, 4):
            variantes = [("fcfs", None), ("sjf", None)] + [("rr", q) for q in args.quantums]
            for sched, q in variantes:
                jobs.append((carga, caminho, sched, q, cpus, "lru", args.quadros, args.custo_troca, args.fault, args.saida))
        for spec in fifo_lru:
            sq, cpus = spec.split("/")
            sched, q = (sq.split(":") + [None])[:2]
            q = int(q) if q else None
            jobs.append((carga, caminho, sched, q, int(cpus), "fifo", args.quadros, args.custo_troca, args.fault, args.saida))
        for qd in args.quadros_extra:
            for cpus in (1, 4):
                for sched, q in (("fcfs", None), ("rr", q2)):
                    for subst in ("lru", "fifo"):
                        jobs.append((carga, caminho, sched, q, cpus, subst, qd, args.custo_troca, args.fault, args.saida))

    print(f"{len(jobs)} execuções, {args.paralelo} em paralelo")
    ini = time.time()
    with Pool(args.paralelo) as pool:
        resultados = pool.map(rodar_um, jobs, chunksize=1)
    print(f"Tempo total: {time.time() - ini:.1f}s")

    with open(os.path.join(args.saida, "todas.json"), "w") as f:
        json.dump(resultados, f, ensure_ascii=False)

    for carga, _ in cargas:
        base = [m for m in resultados if m["carga"] == carga and m["parametros"]["subst"] == "lru"
                and m["parametros"]["quadros"] == args.quadros]
        mk1 = {m["rotulo"]: m["makespan"] for m in base if m["parametros"]["cpus"] == 1}
        linhas = [linha(m, mk1.get(m["rotulo"])) for m in sorted(base, key=lambda m: (m["parametros"]["cpus"], m["rotulo"]))]
        escrever_tabela(linhas, os.path.join(args.saida, f"tabela_{carga}"), f"Execuções base: {carga} (LRU, {args.quadros} quadros)")

        comp = [m for m in resultados if m["carga"] == carga and m["parametros"]["quadros"] == args.quadros]
        pares = []
        for mf in [m for m in comp if m["parametros"]["subst"] == "fifo"]:
            ml = next(m for m in comp if m["parametros"]["subst"] == "lru" and m["rotulo"] == mf["rotulo"]
                      and m["parametros"]["cpus"] == mf["parametros"]["cpus"])
            pares += [linha(ml), linha(mf)]
        if pares:
            escrever_tabela(pares, os.path.join(args.saida, f"fifo_lru_{carga}"), f"FIFO x LRU: {carga}")

        extra = [m for m in resultados if m["carga"] == carga and m["parametros"]["quadros"] != args.quadros]
        if extra:
            todos = sorted(extra + [m for m in comp if m["rotulo"] in {e["rotulo"] for e in extra}],
                           key=lambda m: (m["rotulo"], m["parametros"]["cpus"], m["parametros"]["subst"], m["parametros"]["quadros"]))
            escrever_tabela([linha(m) for m in todos], os.path.join(args.saida, f"quadros_{carga}"), f"Variação de quadros: {carga}")
    print(f"Tabelas em {args.saida}/")


if __name__ == "__main__":
    main()
