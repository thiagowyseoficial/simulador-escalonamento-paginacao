#!/usr/bin/env python3
"""
Gera cargas sintéticas no formato da seção 5.2 do enunciado, para testar o
simulador enquanto os arquivos oficiais (gerados pelo professor) não chegam.

  python gerar_carga.py --cenario A --seed 42 --saida cargas/cenario_A_t0.json
  python gerar_carga.py --cenario B --seed 42 --saida cargas/cenario_B_espacadas.json

Cenário A: 1000 processos chegando em t = 0.
Cenário B: chegadas espalhadas, carga média ~95% da capacidade de 4 CPUs.
"""
import argparse
import json
import random


def gerar_refs(n, paginas, rng):
    """Referências com localidade: anda num conjunto de trabalho pequeno e às vezes salta."""
    refs = []
    pag = rng.randrange(paginas)
    for _ in range(n):
        r = rng.random()
        if r < 0.6:
            pass                                   # repete a mesma página
        elif r < 0.9:
            pag = (pag + rng.choice([-1, 1])) % paginas   # página vizinha
        else:
            pag = rng.randrange(paginas)           # salto
        refs.append(pag)
    return refs


def gerar_processo(pid, chegada, rng, frac_longos):
    longo = rng.random() < frac_longos
    if longo:
        classe, nb = "longo", rng.randint(4, 8)
        cpu_rng, es_rng, paginas = (10, 40), (10, 50), rng.randint(12, 32)
    else:
        classe, nb = "curto", rng.randint(2, 4)
        cpu_rng, es_rng, paginas = (1, 8), (5, 20), rng.randint(4, 12)
    bursts = []
    for i in range(nb):
        n = rng.randint(*cpu_rng)
        bursts.append({"cpu": n, "refs": gerar_refs(n, paginas, rng)})
        if i < nb - 1:
            bursts.append({"es": rng.randint(*es_rng)})
    return {"pid": pid, "chegada": chegada, "classe": classe, "paginas": paginas, "bursts": bursts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cenario", choices=["A", "B"], required=True)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--frac-longos", type=float, default=0.2, dest="frac_longos")
    ap.add_argument("--carga", type=float, default=0.95, help="carga alvo do cenário B (fração de 4 CPUs)")
    ap.add_argument("--saida", required=True)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    procs = [gerar_processo(pid, 0, rng, args.frac_longos) for pid in range(1, args.n + 1)]
    if args.cenario == "B":
        cpu_total = sum(b["cpu"] for p in procs for b in p["bursts"] if "cpu" in b)
        duracao = cpu_total / (4 * args.carga)          # chegam 3,8 unidades de CPU por unidade de tempo
        chegadas = sorted(int(rng.uniform(0, duracao)) for _ in procs)
        for p, c in zip(procs, chegadas):
            p["chegada"] = c
    with open(args.saida, "w") as f:
        json.dump(procs, f)
    cpu_total = sum(b["cpu"] for p in procs for b in p["bursts"] if "cpu" in b)
    print(f"{args.saida}: {len(procs)} processos, CPU total={cpu_total}, "
          f"longos={sum(p['classe'] == 'longo' for p in procs)}, "
          f"última chegada={max(p['chegada'] for p in procs)}")


if __name__ == "__main__":
    main()
