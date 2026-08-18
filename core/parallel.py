"""Worker pool for packing several insertion orders at once."""

import os
from concurrent.futures import TimeoutError
from typing import Any, Optional, Sequence

from .models import Item, LayoutResult, SheetSpec

# Set in each worker by _init_worker.
_ITEMS: dict[str, Item] = {}
_SHEET: Optional[SheetSpec] = None


def _init_worker(items: Sequence[Item], sheet: SheetSpec) -> None:
    global _ITEMS, _SHEET
    _ITEMS = {it.uid: it for it in items}
    _SHEET = sheet


def _variant_index(item: Item, variant: Any) -> int:
    """Position of a variant in its item's list, matched by identity."""
    for i, v in enumerate(item.variants):
        if v is variant:
            return i
    return item.variants.index(variant)


def _pack_order(uids: tuple) -> tuple:
    from .packing import pack_in_order

    order = [_ITEMS[u] for u in uids]
    result = pack_in_order(order, _SHEET)
    records = [
        (p.sheet_index, p.item.uid, _variant_index(p.item, p.variant), p.x, p.y)
        for layout in result.sheets
        for p in layout.placements
    ]
    return result.score, records, [it.uid for it in result.unplaced]


def rebuild_layout(
    packed: tuple, items_by_uid: dict[str, Item], sheet: SheetSpec
) -> LayoutResult:
    from .models import Placement, SheetLayout
    from .packing import shp_translate

    score, records, unplaced_uids = packed
    sheet_count = 1 + max((r[0] for r in records), default=-1)
    layouts = [SheetLayout() for _ in range(sheet_count)]
    for sheet_index, uid, variant_index, x, y in records:
        item = items_by_uid[uid]
        variant = item.variants[variant_index]
        vb = variant.geometry.bounds
        geom = shp_translate(variant.geometry, xoff=x - vb[0], yoff=y - vb[1])
        layouts[sheet_index].placements.append(
            Placement(item, variant, sheet_index, x, y, geom)
        )
    return LayoutResult(
        sheets=layouts,
        score=score,
        unplaced=[items_by_uid[u] for u in unplaced_uids],
    )


def default_workers(item_count: int) -> int:
    """How many workers are worth starting for a job of this size.

    Not one per core: packing leans on memory bandwidth more than arithmetic.
    Twelve identical orders through the pool, 20-thread box:

        1 worker 49.2s | 4: 16.8s | 6: 14.0s | 8: 16.0s | 12: 17.8s
    """
    cpus = os.cpu_count() or 1
    if cpus < 3 or item_count < 6:
        return 1
    return max(2, min(6, cpus // 2))


class OrderPool:
    """Packs insertion orders in parallel, falling back to serial."""

    def __init__(self, items: Sequence[Item], sheet: SheetSpec, workers: int) -> None:
        self.items = list(items)
        self.sheet = sheet
        self.workers = max(1, int(workers))
        self._pool = None

    def __enter__(self) -> "OrderPool":
        if self.workers <= 1:
            return self
        try:
            from concurrent.futures import ProcessPoolExecutor

            self._pool = ProcessPoolExecutor(
                max_workers=self.workers,
                initializer=_init_worker,
                initargs=(self.items, self.sheet),
            )
        except Exception:
            self._pool = None
            self.workers = 1
        return self

    def __exit__(self, *exc_info) -> None:
        self.shutdown()

    def shutdown(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:  # cancel_futures needs 3.9+
                pool.shutdown(wait=False)

    def pack_orders(
        self, orders: Sequence[Sequence[Item]], should_stop=None
    ) -> list[Optional[tuple]]:
        """Pack several orders, one result per order, for ``rebuild_layout``."""
        if not orders:
            return []
        stop = should_stop or (lambda: False)

        if self._pool is None:
            out: list[Optional[tuple]] = []
            for order in orders:
                if stop():
                    out.append(None)
                    continue
                out.append(self._pack_here(order))
            return out

        keys = [tuple(it.uid for it in o) for o in orders]
        try:
            futures = [self._pool.submit(_pack_order, k) for k in keys]
            results: list[Optional[tuple]] = [None] * len(futures)
            pending = set(range(len(futures)))
            while pending:
                if stop():
                    for i in pending:
                        futures[i].cancel()
                    break
                for i in list(pending):
                    fut = futures[i]
                    if fut.done():
                        results[i] = fut.result()
                        pending.discard(i)
                if pending:
                    # Block briefly on one outstanding future instead of
                    # spinning, so a stop lands within a tenth of a second
                    # without burning a core.
                    nxt = next(iter(pending))
                    try:
                        results[nxt] = futures[nxt].result(timeout=0.1)
                        pending.discard(nxt)
                    except TimeoutError:
                        pass
            return results
        except Exception:
            # A worker died. Finish serially and stay serial, rather than
            # killing a long evolve run.
            self.shutdown()
            self.workers = 1
            return [self._pack_here(o) for o in orders]

    #     with ThreadPoolExecutor(max_workers=4) as ex:
    #         return list(ex.map(lambda o: pack_in_order(o, self.sheet), orders))

    def _pack_here(self, order: Sequence[Item]) -> tuple:
        """One worker task, run in this process."""
        from .packing import pack_in_order

        result = pack_in_order(order, self.sheet)
        records = [
            (p.sheet_index, p.item.uid, _variant_index(p.item, p.variant), p.x, p.y)
            for layout in result.sheets
            for p in layout.placements
        ]
        return result.score, records, [it.uid for it in result.unplaced]
