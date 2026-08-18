"""SVGnest-style genetic algorithm over the part insertion order.

Built entirely on the greedy engine's public seam. An individual is a
permutation of the items and its fitness is the full layout score of
``packing.pack_in_order``. The GA and the multi-pass heuristic
optimise the exact same objective and their results are directly comparable.
"""

import random
from typing import Any, Callable, Optional, Sequence

from .models import Item, LayoutResult, SheetSpec
from .packing import finish_layout, item_metric, optimize_layout
from .parallel import OrderPool, default_workers, rebuild_layout


def default_population(workers: int) -> int:
    if workers <= 1:
        return 8
    return max(8, min(20, round(workers * 4 / 3)))


def ga_generations(
    items: Sequence[Item],
    sheet: SheetSpec,
    seed: int = 42,
    population: Optional[int] = None,
    workers: Optional[int] = None,
    should_stop: Optional[Callable[[], bool]] = None,
):
    """Infinite generator driving the genetic algorithm. Each ``next()`` breeds
    and evaluates one generation and yields the best ``LayoutResult``
    found so far.
    """
    items = list(items)
    stop = should_stop or (lambda: False)
    if workers is None:
        workers = default_workers(len(items))
    if population is None:
        population = default_population(workers)
    population = max(4, population)

    rng = random.Random(seed)
    items_by_uid = {it.uid: it for it in items}
    packed: dict[tuple, tuple] = {}

    def key_of(order: Sequence[Item]) -> tuple:
        return tuple(it.uid for it in order)

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

    def tournament(ranked: list[tuple[tuple, list[Item]]]) -> list[Item]:
        a, b = rng.sample(range(len(ranked)), 2)
        return ranked[min(a, b)][1]  # list is sorted: lower index = fitter

    with OrderPool(items, sheet, workers) as pool:

        def evaluate_all(orders: list[list[Item]]) -> bool:
            """Pack whatever is not cached yet. False if the batch was cut
            short by a stop request."""
            missing: list[list[Item]] = []
            seen: set[tuple] = set()
            for order in orders:
                key = key_of(order)
                if key not in packed and key not in seen:
                    seen.add(key)
                    missing.append(order)
            if not missing:
                return True
            fresh = pool.pack_orders(missing, should_stop=stop)
            complete = True
            for order, result in zip(missing, fresh):
                if result is None:
                    complete = False
                    continue
                packed[key_of(order)] = result
            return complete

        def rank(orders: list[list[Item]]) -> list[tuple[tuple, list[Item]]]:
            scored = [(packed[key_of(o)][0], o) for o in orders if key_of(o) in packed]
            scored.sort(key=lambda t: t[0])
            return scored

        def best_of(orders: list[list[Item]]) -> Optional[LayoutResult]:
            """The best layout among whichever of these orders got packed."""
            scored = rank(orders)
            if not scored:
                return None
            return rebuild_layout(packed[key_of(scored[0][1])], items_by_uid, sheet)

        def forget_all_but(orders: list[list[Item]]) -> None:
            live = {key_of(o) for o in orders}
            for key in list(packed):
                if key not in live:
                    del packed[key]

        # Pack the three deterministic heuristic orders on their own first, and
        # show the best of them straight away
        head = pop[: min(3, len(pop))]
        complete = evaluate_all(head)
        first = best_of(head)
        if first is not None:
            yield first
        if not complete:
            return

        complete = evaluate_all(pop)
        if not complete:
            layout = best_of(pop)
            if layout is not None:
                yield layout
            return
        ranked = rank(pop)

        while True:
            if stop():
                return
            elite_n = max(2, population // 4)
            offspring = [
                mutate(crossover(tournament(ranked), tournament(ranked)))
                for _ in range(population - elite_n)
            ]
            merged = [o for _, o in ranked[:elite_n]] + offspring
            complete = evaluate_all(merged)
            ranked = rank(merged)
            if not ranked:
                return
            winner = packed[key_of(ranked[0][1])]
            forget_all_but(merged)
            yield rebuild_layout(winner, items_by_uid, sheet)
            if not complete:
                return


def optimize_layout_ga(
    items: Sequence[Item],
    sheet: SheetSpec,
    seed: int = 42,
    population: Optional[int] = None,
    generations: Optional[int] = None,
    on_pass: Optional[Any] = None,
    workers: Optional[int] = None,
) -> LayoutResult:
    """Run ``ga_generations`` for a fixed number of generations and polish
    the winner. Slower than ``packing.optimize_layout``, since every
    generation packs a batch of complete layouts, but often tighter on hard
    jobs."""
    items = list(items)
    if generations is None:
        generations = max(sheet.passes, 4)
    if len(items) <= 2:
        return optimize_layout(items, sheet, seed=seed, on_pass=on_pass)

    it = ga_generations(
        items, sheet, seed=seed, population=population, workers=workers
    )
    best: Optional[LayoutResult] = None
    for gen in range(generations):
        if on_pass is not None:
            on_pass(gen, generations)
        nxt = next(it, None)
        if nxt is None:
            break
        best = nxt
    it.close()

    if best is None:
        return optimize_layout(items, sheet, seed=seed, on_pass=on_pass)
    return finish_layout(best, sheet, seed=seed)
