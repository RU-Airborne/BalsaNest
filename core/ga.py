"""SVGnest-style genetic algorithm over the part insertion order.

Built entirely on the greedy engine's public seam: an individual is a
permutation of the items, and its fitness is the full layout score of
:func:`.packing.pack_in_order` -- so the GA and the multi-pass heuristic
optimise the exact same objective and their results are directly comparable.
"""

from __future__ import annotations

import random
from typing import Any, Optional, Sequence

from .models import Item, LayoutResult, SheetSpec
from .packing import item_metric, optimize_layout, pack_in_order, polish_layout


def ga_generations(
    items: Sequence[Item],
    sheet: SheetSpec,
    seed: int = 42,
    population: int = 8,
):
    """Infinite generator driving the genetic algorithm. Each ``next()`` breeds
    and evaluates one generation and yields the best :class:`LayoutResult`
    found so far (unpolished).

    The population is seeded with the deterministic heuristic orders (the GA
    can only match or beat the heuristic), evolved with order crossover (OX) +
    swap mutation and (mu + lambda) elitist survival. Evaluations are cached by
    order, so re-visited permutations are free. The caller decides when to stop
    and should run :func:`.packing.polish_layout` on the final result."""
    items = list(items)
    rng = random.Random(seed)
    population = max(4, population)
    cache: dict[tuple, LayoutResult] = {}

    def evaluate(order: list[Item]) -> LayoutResult:
        key = tuple(it.uid for it in order)
        result = cache.get(key)
        if result is None:
            result = pack_in_order(order, sheet)
            cache[key] = result
        return result

    # Seed population: deterministic heuristic orders + random shuffles.
    pop: list[list[Item]] = []
    for mode in range(3):
        r = random.Random(seed + mode)
        pop.append(sorted(items, key=lambda it: item_metric(it, mode, r)))
    while len(pop) < population:
        ind = items[:]
        rng.shuffle(ind)
        pop.append(ind)

    def crossover(a: list[Item], b: list[Item]) -> list[Item]:
        n = len(a)
        i, j = sorted(rng.sample(range(n), 2))
        mid = a[i : j + 1]
        used = {it.uid for it in mid}
        rest = [it for it in b if it.uid not in used]
        return rest[:i] + mid + rest[i:]

    def mutate(ind: list[Item]) -> list[Item]:
        ind = ind[:]
        n = len(ind)
        for _ in range(max(1, n // 8)):
            if rng.random() < 0.85:
                i, j = rng.randrange(n), rng.randrange(n)
                ind[i], ind[j] = ind[j], ind[i]
        return ind

    def tournament(scored: list[tuple[tuple, list[Item]]]) -> list[Item]:
        a, b = rng.sample(range(len(scored)), 2)
        return scored[min(a, b)][1]  # list is sorted: lower index = fitter

    scored = sorted(((evaluate(o).score, o) for o in pop), key=lambda t: t[0])
    while True:
        elite_n = max(2, population // 4)
        offspring = [
            mutate(crossover(tournament(scored), tournament(scored)))
            for _ in range(population - elite_n)
        ]
        merged = [o for _, o in scored[:elite_n]] + offspring
        scored = sorted(((evaluate(o).score, o) for o in merged), key=lambda t: t[0])
        yield evaluate(scored[0][1])


def optimize_layout_ga(
    items: Sequence[Item],
    sheet: SheetSpec,
    seed: int = 42,
    population: int = 8,
    generations: Optional[int] = None,
    on_pass: Optional[Any] = None,
) -> LayoutResult:
    """Run :func:`ga_generations` for a fixed number of generations and polish
    the winner. Considerably slower than :func:`.packing.optimize_layout` --
    every generation packs several full layouts -- but often tighter on hard
    jobs."""
    items = list(items)
    if generations is None:
        generations = max(sheet.passes, 4)
    if len(items) <= 2:
        return optimize_layout(items, sheet, seed=seed, on_pass=on_pass)

    it = ga_generations(items, sheet, seed=seed, population=population)
    best: Optional[LayoutResult] = None
    for gen in range(generations):
        if on_pass is not None:
            on_pass(gen, generations)
        best = next(it)

    assert best is not None
    return polish_layout(best, sheet)
