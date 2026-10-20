"""Two fixed lineups share recruitment, exploration and rank-up mechanics."""
from dataclasses import dataclass
from enum import Enum


class RecruitmentPolicy(str, Enum):
    NONE = 'none'
    TEACHER_FOR_AROWELL = 'teacher_for_arowell'
    RESTORE_SHADOW_ROSE = 'restore_shadow_rose'


DARK_TWINS_SCENES = frozenset('''
hero_twins recruited_twins selected_name_twins
hero_arowell recruited_arowell selected_name_arowell
recruited_teacher filter_icon_thief_filter filter_thief_selected
bench_teacher bench_arowell bench_selected formation_knight replace_button
'''.split())
BUDGET_SCENES = frozenset('''
selected_name_teacher recruited_teacher_initial
hero_shadow_rose selected_name_shadow_rose recruited_shadow_rose
filter_icon_knight_filter filter_knight_selected recruited_shadow_rose_all
'''.split())


@dataclass(frozen=True, slots=True)
class Lineup:
    key: str
    label: str
    heroes: tuple[tuple[str, str, str], ...]
    rank_priority: tuple[str, ...]
    scenes: frozenset[str]
    recruitment_policy: RecruitmentPolicy
    departure_hero: str


DARK_TWINS = Lineup(
    'dark_twins', '暗双子队',
    (('twins', 'thief', 'dark'), ('ivanna', 'soul', 'fire'),
     ('xiaochun', 'warrior', 'water'), ('arowell', 'knight', 'light')),
    ('xiaochun', 'twins', 'ivanna'), DARK_TWINS_SCENES, RecruitmentPolicy.TEACHER_FOR_AROWELL, 'arowell',
)
BUDGET = Lineup(
    'budget', '平民替换队',
    (('teacher', 'thief', 'dark'), ('ivanna', 'soul', 'fire'),
     ('xiaochun', 'warrior', 'water'), ('shadow_rose', 'knight', 'dark')),
    ('xiaochun', 'shadow_rose', 'teacher'), BUDGET_SCENES, RecruitmentPolicy.RESTORE_SHADOW_ROSE, 'shadow_rose',
)
LINEUPS = (DARK_TWINS, BUDGET)


def get_lineup(key: str) -> Lineup:
    for lineup in LINEUPS:
        if key == lineup.key:
            return lineup
    raise ValueError(f'unknown Chaos lineup: {key}')
