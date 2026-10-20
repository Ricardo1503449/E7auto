from dataclasses import dataclass, field

from .contracts import Node
from .lineups import DARK_TWINS, Lineup


@dataclass(slots=True)
class RoundState:
    floor: int = 1
    node_kind: str | None = None
    battle_floor: int | None = None
    ranks: dict[str, int] = field(default_factory=lambda: dict.fromkeys(("twins", "ivanna", "xiaochun", "arowell"), 1))
    rank_priority: tuple[str, ...] = DARK_TWINS.rank_priority
    auto_confirmed: bool = False
    pending_teacher: bool = False
    departed_heroes: set[str] = field(default_factory=set)
    second_floor_voucher_done: bool = False
    reward_stage: str | None = None
    counted: bool = False

    def record_departure(self, hero: str) -> None:
        """Called only after the submitted hero's Missing result is confirmed."""
        del self.ranks[hero]
        self.departed_heroes.add(hero)
        if hero == 'arowell':
            self.pending_teacher = False

    def next_rank_hero(self) -> str | None:
        return next((hero for hero in self.rank_priority if hero in self.ranks and self.ranks[hero] < 5), None)

    @classmethod
    def for_lineup(cls, lineup: Lineup) -> 'RoundState':
        return cls(ranks=dict.fromkeys((hero[0] for hero in lineup.heroes), 1),
                   rank_priority=lineup.rank_priority)


def choose_node(nodes: tuple[Node, ...]) -> Node:
    if not nodes:
        raise ValueError("no available node")
    if len(nodes) == 1:
        return nodes[0]
    priority = {"rest": 0, "elite": 1, "loot": 2, "event": 2, "normal": 3, "shop": 4}
    ordinary = [n for n in nodes if n.kind in priority]
    if not ordinary:
        raise ValueError("multiple boss candidates")
    return min(ordinary, key=lambda n: (priority[n.kind], n.detection.bounds.y+n.detection.bounds.height/2))
