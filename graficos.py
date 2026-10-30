#!/usr/bin/env python3
"""
Gera os gráficos do relatório a partir de resultados/todas.json e as figuras
de Gantt a partir dos CSVs.

  python graficos.py --resultados resultados --saida figuras
"""
import argparse
import csv
import glob
import json
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

CORES = {"FCFS": "#4C72B0", "SJF": "#DD8452", "RR": "#55A868"}
QUADROS_BASE = 256   # memória das execuções base (seção 11)


def cor(rotulo):
    return CORES.get(rotulo.split()[0], "#8172B2")


def carregar(resultados):
    with open(os.path.join(resultados, "todas.json")) as f:
        return json.load(f)


def base(ms, carga, quadros=QUADROS_BASE):
    return [m for m in ms if m["carga"] == carga and m["parametros"]["subst"] == "lru"
            and m["parametros"]["quadros"] == quadros]


def grafico_barras_por_sched(ms, carga, metrica, titulo, ylabel, saida, log=False):
    b = base(ms, carga)
    rotulos = sorted({m["rotulo"] for m in b}, key=lambda r: (r.split()[0] != "FCFS", r.split()[0] != "SJF", r))
    fig, ax = plt.subplots(figsize=(9, 4.5))
    w = 0.38
    for i, cpus in enumerate((1, 4)):
        vals = [next(m[metrica] for m in b if m["rotulo"] == r and m["parametros"]["cpus"] == cpus) for r in rotulos]
        xs = [x + (i - 0.5) * w for x in range(len(rotulos))]
        bars = ax.bar(xs, vals, w, label=f"{cpus} CPU{'s' if cpus > 1 else ''}", color=[cor(r) for r in rotulos],
                      alpha=1.0 if cpus == 1 else 0.55, edgecolor="black", linewidth=0.5)
        for bar, v in zip(bars, vals):
            ax.annotate(f"{v:,.0f}", (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        ha="center", va="bottom", fontsize=7, xytext=(0, 2), textcoords="offset points")
    ax.set_xticks(range(len(rotulos)))
    ax.set_xticklabels(rotulos)
    ax.set_ylabel(ylabel)
    ax.set_title(f"{titulo} — {carga}")
    if log:
        ax.set_yscale("log")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(saida, dpi=150)
    plt.close(fig)


def grafico_quantum(ms, carga, saida):
    b = [m for m in base(ms, carga) if m["parametros"]["sched"] == "rr"]
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    for cpus, estilo in ((1, "o-"), (4, "s--")):
        sub = sorted([m for m in b if m["parametros"]["cpus"] == cpus], key=lambda m: m["parametros"]["quantum"])
        qs = [m["parametros"]["quantum"] for m in sub]
        for ax, (met, nome) in zip(axes, (("espera_media", "Espera média"), ("resposta_media", "Resposta média"),
                                          ("trocas_total", "Trocas de contexto"))):
            ax.plot(range(len(qs)), [m[met] for m in sub], estilo, label=f"{cpus} CPU{'s' if cpus > 1 else ''}")
            ax.set_xlabel("quantum")
            ax.set_title(nome)
            ax.set_xticks(range(len(qs)))
            ax.set_xticklabels([str(q) for q in qs])
            ax.grid(alpha=0.3)
    axes[0].legend()
    fig.suptitle(f"Efeito do quantum no RR — {carga}")
    fig.tight_layout()
    fig.savefig(saida, dpi=150)
    plt.close(fig)


def grafico_speedup(ms, cargas, saida):
    fig, ax = plt.subplots(figsize=(9, 4.5))
    w = 0.8 / len(cargas)
    rotulos = None
    for i, carga in enumerate(cargas):
        b = base(ms, carga)
        rotulos = sorted({m["rotulo"] for m in b}, key=lambda r: (r.split()[0] != "FCFS", r.split()[0] != "SJF", r))
        sp = []
        for r in rotulos:
            m1 = next(m for m in b if m["rotulo"] == r and m["parametros"]["cpus"] == 1)
            m4 = next(m for m in b if m["rotulo"] == r and m["parametros"]["cpus"] == 4)
            sp.append(m1["makespan"] / m4["makespan"])
        xs = [x + (i - (len(cargas) - 1) / 2) * w for x in range(len(rotulos))]
        bars = ax.bar(xs, sp, w, label=carga, edgecolor="black", linewidth=0.5)
        for bar, v in zip(bars, sp):
            ax.annotate(f"{v:.2f}", (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        ha="center", va="bottom", fontsize=7, xytext=(0, 2), textcoords="offset points")
    ax.axhline(4, color="red", linestyle=":", label="ideal (4)")
    ax.set_xticks(range(len(rotulos)))
    ax.set_xticklabels(rotulos)
    ax.set_ylabel("speedup = makespan(1 CPU) / makespan(4 CPUs)")
    ax.set_title("Speedup por escalonador")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(saida, dpi=150)
    plt.close(fig)


def grafico_fifo_lru(ms, carga, saida):
    comp = [m for m in ms if m["carga"] == carga and m["parametros"]["quadros"] == QUADROS_BASE]
    fifo = [m for m in comp if m["parametros"]["subst"] == "fifo"]
    if not fifo:
        return
    rot = [f"{m['rotulo']}\n{m['parametros']['cpus']} CPU(s)" for m in fifo]
    lru = [next(x for x in comp if x["parametros"]["subst"] == "lru" and x["rotulo"] == m["rotulo"]
                and x["parametros"]["cpus"] == m["parametros"]["cpus"]) for m in fifo]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, (met, nome) in zip(axes, (("page_faults", "Page faults"), ("turnaround_medio", "Turnaround médio"))):
        xs = range(len(rot))
        ax.bar([x - 0.2 for x in xs], [m[met] for m in fifo], 0.4, label="FIFO", color="#C44E52", edgecolor="black", linewidth=0.5)
        ax.bar([x + 0.2 for x in xs], [m[met] for m in lru], 0.4, label="LRU", color="#4C72B0", edgecolor="black", linewidth=0.5)
        ax.set_xticks(list(xs))
        ax.set_xticklabels(rot, fontsize=8)
        ax.set_title(nome)
        ax.grid(axis="y", alpha=0.3)
    axes[0].legend()
    fig.suptitle(f"FIFO x LRU — {carga}")
    fig.tight_layout()
    fig.savefig(saida, dpi=150)
    plt.close(fig)


def grafico_classes(ms, carga, saida):
    """Espera média de curtos x longos por escalonador (injustiça / efeito comboio)."""
    b = base(ms, carga)
    rotulos = sorted({m["rotulo"] for m in b}, key=lambda r: (r.split()[0] != "FCFS", r.split()[0] != "SJF", r))
    classes = sorted({c for m in b for c in m["por_classe"]})
    if len(classes) < 2:
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=False)
    for ax, cpus in zip(axes, (1, 4)):
        w = 0.8 / len(classes)
        for i, cl in enumerate(classes):
            vals = [next(m for m in b if m["rotulo"] == r and m["parametros"]["cpus"] == cpus)["por_classe"][cl]["espera_media"]
                    for r in rotulos]
            ax.bar([x + (i - (len(classes) - 1) / 2) * w for x in range(len(rotulos))], vals, w, label=cl,
                   edgecolor="black", linewidth=0.5)
        ax.set_xticks(range(len(rotulos)))
        ax.set_xticklabels(rotulos, fontsize=8)
        ax.set_title(f"{cpus} CPU{'s' if cpus > 1 else ''}")
        ax.set_ylabel("espera média")
        ax.grid(axis="y", alpha=0.3)
    axes[0].legend(title="classe")
    fig.suptitle(f"Espera média por classe de processo — {carga}")
    fig.tight_layout()
    fig.savefig(saida, dpi=150)
    plt.close(fig)


def grafico_quadros(ms, carga, saida):
    """Page faults e makespan em função do número de quadros (FCFS, LRU), 1 e 4 CPUs."""
    sel = [m for m in ms if m["carga"] == carga and m["parametros"]["sched"] == "fcfs" and m["parametros"]["subst"] == "lru"]
    quadros = sorted({m["parametros"]["quadros"] for m in sel})
    if len(quadros) < 2:
        return
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    for cpus, estilo in ((1, "o-"), (4, "s--")):
        sub = sorted([m for m in sel if m["parametros"]["cpus"] == cpus], key=lambda m: m["parametros"]["quadros"])
        for ax, (met, nome) in zip(axes, (("page_faults", "Page faults"), ("makespan", "Makespan"),
                                          ("turnaround_medio", "Turnaround médio"))):
            ax.plot(range(len(sub)), [m[met] for m in sub], estilo, label=f"{cpus} CPU{'s' if cpus > 1 else ''}")
            ax.set_xticks(range(len(sub)))
            ax.set_xticklabels([str(m["parametros"]["quadros"]) for m in sub])
            ax.set_xlabel("quadros")
            ax.set_title(nome)
            ax.grid(alpha=0.3)
    axes[0].legend()
    fig.suptitle(f"Efeito do número de quadros (FCFS, LRU) — {carga}")
    fig.tight_layout()
    fig.savefig(saida, dpi=150)
    plt.close(fig)


def figura_gantt(csv_path, saida, titulo):
    faixas = defaultdict(list)
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            faixas[int(r["cpu"])].append((int(r["inicio"]), int(r["fim"]), r["evento"]))
    ncpu = max(faixas) + 1
    fig, ax = plt.subplots(figsize=(16, 1.2 + 1.1 * ncpu))
    pids = sorted({ev for v in faixas.values() for _, _, ev in v if ev.startswith("P")}, key=lambda s: int(s[1:]))
    cmap = plt.get_cmap("tab20")
    cores = {p: cmap(i % 20) for i, p in enumerate(pids)}
    for c in range(ncpu):
        for ini, fim, ev in faixas[c]:
            if ev == "TROCA":
                ax.broken_barh([(ini, fim - ini)], (c - 0.4, 0.8), facecolors="black")
            elif ev == "OCIOSA":
                ax.broken_barh([(ini, fim - ini)], (c - 0.4, 0.8), facecolors="white", edgecolors="gray", hatch="//")
            else:
                ax.broken_barh([(ini, fim - ini)], (c - 0.4, 0.8), facecolors=cores[ev], edgecolors="black", linewidth=0.3)
                if fim - ini >= 4:
                    ax.text((ini + fim) / 2, c, ev, ha="center", va="center", fontsize=6)
    ax.set_yticks(range(ncpu))
    ax.set_yticklabels([f"CPU {c}" for c in range(ncpu)])
    ax.set_ylim(-0.6, ncpu - 0.4)
    ax.set_xlim(0, max(fim for v in faixas.values() for _, fim, _ in v))
    ax.set_xlabel("tempo")
    ax.set_title(f"{titulo}  (preto = troca de contexto, hachurado = ociosa)")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(saida, dpi=150)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resultados", default="resultados")
    ap.add_argument("--saida", default="figuras")
    ap.add_argument("--gantt-config", default="fcfs", dest="gantt_config",
                    help="prefixo da config cujo Gantt vira figura (ex.: fcfs, sjf, rr_q10)")
    args = ap.parse_args()
    os.makedirs(args.saida, exist_ok=True)
    ms = carregar(args.resultados)
    cargas = sorted({m["carga"] for m in ms})

    for carga in cargas:
        grafico_barras_por_sched(ms, carga, "espera_media", "Espera média por escalonador", "espera média (unidades)",
                                 os.path.join(args.saida, f"espera_{carga}.png"))
        grafico_barras_por_sched(ms, carga, "resposta_media", "Tempo de resposta médio", "resposta média (unidades)",
                                 os.path.join(args.saida, f"resposta_{carga}.png"))
        grafico_barras_por_sched(ms, carga, "page_faults", "Page faults por escalonador", "page faults",
                                 os.path.join(args.saida, f"faults_{carga}.png"))
        grafico_quantum(ms, carga, os.path.join(args.saida, f"quantum_{carga}.png"))
        grafico_fifo_lru(ms, carga, os.path.join(args.saida, f"fifo_lru_{carga}.png"))
        grafico_classes(ms, carga, os.path.join(args.saida, f"classes_{carga}.png"))
        grafico_quadros(ms, carga, os.path.join(args.saida, f"quadros_{carga}.png"))
        for cpus in (1, 4):
            padrao = os.path.join(args.resultados, carga, f"gantt_{args.gantt_config}_cpus{cpus}_lru_{QUADROS_BASE}quadros_*.csv")
            for csv_path in glob.glob(padrao):
                nome = os.path.basename(csv_path)[len("gantt_"):-4]
                figura_gantt(csv_path, os.path.join(args.saida, f"gantt_{carga}_{nome}.png"), f"Gantt 0–200: {carga}, {nome}")
    grafico_speedup(ms, cargas, os.path.join(args.saida, "speedup.png"))
    print(f"Figuras em {args.saida}/: " + ", ".join(sorted(os.listdir(args.saida))))


if __name__ == "__main__":
    main()
