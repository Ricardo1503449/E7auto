"""Strict scene bindings; load selected Chaos, network and opt-in daily-reset resources."""
from __future__ import annotations

from dataclasses import dataclass, fields
from math import isfinite
from pathlib import Path
from typing import Mapping

import yaml

from e7auto.configuration.models import AppConfig, ConfigError
from e7auto.configuration.schema import NAVIGATION_TEMPLATE_KEYS
from e7auto.configuration.components import load_component_layouts
from e7auto.configuration.daily_reset import DailyResetSettings, load_daily_reset
from e7auto.core.types import Rect
from e7auto.resources.manifest import load_template_manifest
from .lineups import DARK_TWINS, LINEUPS, Lineup, get_lineup


EVENT_IDENTITIES = frozenset('''
event_torch event_mimic event_trap event_tablet event_vestment event_golem
event_giant_eye event_prison event_cage event_altar event_sword event_cairn
event_mushroom event_library event_flame event_torn_map event_torn_map_path event_mural event_blood_shelter
event_passage event_observatory event_backpack event_spirit_door event_ghost
event_erased_records event_fairy_dance event_blue_flame_altar event_broken_mask event_ancient_corridor
event_angry_inscription event_ancient_stele event_blood_meteorite event_windy_cliff
event_forked_path event_starlight_flower event_grieving_woman
event_withered_garden event_prayer_room event_golden_patterns event_clear_pond event_clear_pond_low_health
event_warning_sign event_warning_sign_low_health event_mysterious_footprints event_old_armory event_blood_circle
event_grimoire event_broken_candlestick event_red_phantom event_broken_cage
event_sharp_teeth event_collapsed_stairs event_bone_throne
'''.split())


@dataclass(frozen=True, slots=True)
class Scene:
    template_key: str
    region: Rect
    threshold: float
    structure: float
    action: tuple[float, float] | None
    relative_offset: tuple[int, int, int, int] | None = None
    action_offset: tuple[int, int] = (0, 0)


@dataclass(frozen=True, slots=True)
class ChaosSettings:
    scenes: Mapping[str, Scene]
    timing: Mapping[str, int]
    geometry: Mapping[str, object]
    auto_motion: Mapping[str, object]
    heroes: tuple[Mapping[str, str], ...]
    loot_priorities: Mapping[str, int]
    regions: Mapping[str, Rect]
    stage_selection: Mapping[str, object]
    pending_loot_templates: tuple[str, ...]
    text_triangle: Mapping[str, float]
    loot_recognition: Mapping[str, object]
    event_recognition: Mapping[str, object]
    daily_reset: DailyResetSettings
    target_stage: int = 1
    lineup: Lineup = DARK_TWINS

    @property
    def available_loot(self) -> tuple[str, ...]:
        return tuple(key for key in self.loot_priorities if key in self.scenes)


@dataclass(frozen=True, slots=True, kw_only=True)
class ChaosConfig(AppConfig):
    chaos: ChaosSettings


def _rect(value: object, width: int, height: int) -> Rect:
    if not isinstance(value, list) or len(value) != 4 or any(type(v) is not int for v in value):
        raise ValueError("region must contain four integers")
    r = Rect(*value)
    if not (0 <= r.x < r.right <= width and 0 <= r.y < r.bottom <= height):
        raise ValueError(f"region outside baseline: {value}")
    return r


LOBBY_STAGE_SCENES = {1: "stage_one", 2: "stage_two", 3: "stage_three", 4: "stage_four", 5: "stage_five"}

COMPONENT_TEMPLATES = frozenset('''
hero_xiaochun hero_twins hero_ivanna hero_arowell hero_teacher hero_shadow_rose
bench_teacher bench_arowell journey_close rank_close contract
ticket_thief ticket_soul ticket_warrior ticket_knight ticket_all
purchase_item purchase_health_restore rank_value_1 rank_value_2 rank_value_3
rank_value_4 rank_value_5
'''.split())


def with_chaos_config(config: AppConfig, path: Path | None = None, *, target_stage: int = 1,
                      lineup_id: str = 'dark_twins') -> ChaosConfig:
    try:
        path = path or config.source_path.parent / "chaos.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf8"))
        size = config.baseline_client_size
        if raw["schema_version"] != 1 or raw["feature"] != "chaos" or raw["baseline"] != [size.width, size.height]:
            raise ValueError("invalid Chaos schema/feature/baseline")
        lineup = get_lineup(lineup_id)
        bindings = raw["templates"]
        if type(target_stage) is not int or target_stage not in LOBBY_STAGE_SCENES:
            raise ValueError("target_stage must be an integer from 1 to 5")
        lobby_scenes = raw["stage_selection"]["lobby_scenes"]
        if (not isinstance(lobby_scenes, dict) or lobby_scenes != LOBBY_STAGE_SCENES
                or any(type(n) is not int or LOBBY_STAGE_SCENES.get(n) != key for n, key in lobby_scenes.items())):
            raise ValueError("all five lobby stage scene bindings are required")
        if target_stage not in lobby_scenes:
            raise ValueError(f"第{target_stage}阶段缺少大厅识别资源，无法确认目标阶段")
        required = COMMON_SCENES | lineup.scenes | set(lobby_scenes.values())
        missing = required - set(bindings)
        unknown = set(bindings) - (KNOWN_SCENES | set(lobby_scenes.values()))
        if missing or unknown:
            raise ValueError(f"missing/unknown scenes: {missing | unknown}")
        # Resolve only the selected lineup before loading any image or hash.
        bindings = {key: value for key, value in bindings.items() if key in required}
        regions = {k: _rect(v, size.width, size.height) for k, v in raw["rois"].items()}
        resource_keys = set()
        aliases = {"camp_leave": "shop_leave", "supply_leave": "shop_leave",
                   "boss_choose_reward": "elite_choose_reward", "shop_leave_confirm": "unclaimed_confirm",
                   "event_reward_close": "rank_close", "investment_entry": "shop_health_entry",
                   "story_skip_confirm": "unclaimed_confirm"}
        for name, value in bindings.items():
            resource = value["resource"]
            expected = {"feature": "common" if name in ("network_connection_abnormal", "network_retry") else "chaos",
                        "key": aliases.get(name, name)}
            if resource != expected:
                raise ValueError(f"unsupported scene/resource binding: {name}: {resource}")
            if resource["feature"] == "chaos":
                resource_keys.add(resource["key"])
            elif resource != {"feature": "common", "key": "network_retry"} and resource != {"feature": "common", "key": "network_connection_abnormal"}:
                raise ValueError(f"unsupported resource dependency: {resource}")
        registered, sizes = load_template_manifest(
            config.template_manifest_paths["chaos"], (size.width, size.height),
            prefix="chaos:", keys=frozenset(resource_keys),
        )
        components = load_component_layouts(raw['component_matching'], sizes,
                                            COMPONENT_TEMPLATES, prefix='chaos:')
        paths = {k: config.template_paths[k] for k in
                 {"network_connection_abnormal", "network_retry"} | NAVIGATION_TEMPLATE_KEYS}
        paths.update(registered)
        scenes = {}
        for name, value in bindings.items():
            resource = value["resource"]
            key = "chaos:" + resource["key"] if resource["feature"] == "chaos" else resource["key"]
            if key not in paths:
                raise ValueError(f"unknown resource: {name}: {key}")
            region = regions[value["search_roi"]]
            if key in sizes and (sizes[key][0] > region.width or sizes[key][1] > region.height):
                raise ValueError(f"scene region cannot contain template: {name}")
            scores = [value["threshold"], value["structure_threshold"]]
            if any(type(v) not in (int, float) or not isfinite(v) or not 0 < v <= 1 for v in scores):
                raise ValueError(f"invalid thresholds: {name}")
            action = value.get("action")
            if action is not None and (not isinstance(action, list) or len(action) != 2 or any(type(v) not in (int, float) or not isfinite(v) or not 0 <= v <= 1 for v in action)):
                raise ValueError(f"invalid click anchor: {name}")
            action_offset = value.get("action_offset", [0, 0])
            if (not isinstance(action_offset, list) or len(action_offset) != 2
                    or any(type(v) is not int for v in action_offset)
                    or abs(action_offset[0]) >= size.width or abs(action_offset[1]) >= size.height
                    or (action is None and action_offset != [0, 0])):
                raise ValueError(f"invalid click offset: {name}")
            relative = value.get("relative_roi")
            offset = None
            if relative is not None:
                if relative.get("anchor") != "formation_knight" or len(relative.get("offset", ())) != 4 or any(type(v) is not int for v in relative["offset"]):
                    raise ValueError(f"invalid relative search region: {name}")
                offset = tuple(relative["offset"])
            scenes[name] = Scene(key, region, *scores, tuple(action) if action else None, offset, tuple(action_offset))
        rank_area = regions["rank_result"]
        before, after = regions["rank_result_before"], regions["rank_result_after"]
        if (before.right > after.x or any(
                not (rank_area.x <= area.x < area.right <= rank_area.right
                     and rank_area.y <= area.y < area.bottom <= rank_area.bottom)
                for area in (before, after))):
            raise ValueError("invalid rank result columns")
        for rank in range(1, 6):
            scene = scenes[f"rank_value_{rank}"]
            width, height = sizes[scene.template_key]
            if (scene.action is not None or scene.region != rank_area
                    or width > after.width or height > after.height
                    or (rank < 5 and (width > before.width or height > before.height))):
                raise ValueError("invalid single-rank scene or result column size")
        timing = raw["timing"]
        required_timing = {"poll_ms", "stable_frames", "page_timeout_ms", "entry_timeout_ms", "battle_timeout_ms", "settlement_poll_ms", "network_poll_ms", "action_settle_ms",
                           "event_close_stable_ms", "event_close_retry_ms", "event_close_max_attempts", "event_entry_settle_ms",
                           "event_choice_retry_ms", "event_choice_max_attempts",
                           "map_node_retry_ms", "map_node_max_attempts",
                           "battle_signal_poll_ms", "battle_result_settle_ms", "loot_entry_settle_ms", "supply_loot_entry_settle_ms",
                           "story_timeout_ms", "story_retry_ms", "story_max_attempts"}
        if set(timing) != required_timing or any(type(v) is not int or v <= 0 for v in timing.values()):
            raise ValueError("invalid Chaos timing")
        if timing["stable_frames"] < 3 or timing["network_poll_ms"] > 1000:
            raise ValueError("stable evidence and network polling constraints violated")
        if not timing["action_settle_ms"] < timing["story_retry_ms"] < timing["story_timeout_ms"]:
            raise ValueError("invalid story timing order")
        if not timing["event_close_stable_ms"] < timing["event_close_retry_ms"] < timing["page_timeout_ms"]:
            raise ValueError("invalid event close timing order")
        if not timing["action_settle_ms"] < timing["event_choice_retry_ms"] < timing["page_timeout_ms"]:
            raise ValueError("invalid event choice timing order")
        if not timing["action_settle_ms"] < timing["map_node_retry_ms"] < timing["page_timeout_ms"]:
            raise ValueError("invalid map node timing order")
        loot = raw["loot_priorities"]
        expected_loot = LOOT_IDENTITIES
        if set(loot) != expected_loot or any(type(v) is not int or v not in (0, 1, 2) for v in loot.values()):
            raise ValueError("invalid loot policy")
        pending = raw["pending_loot_templates"]
        if (not isinstance(pending, list) or any(not isinstance(k, str) for k in pending)
                or len(set(pending)) != len(pending) or set(pending) != expected_loot - set(scenes)):
            raise ValueError("pending loot must exactly name identities without canonical templates")
        motion = raw["auto_motion"]
        motion_region = _rect(motion["roi"], size.width, size.height)
        integers = ("white_min", "channel_spread_max", "angle_step", "period_degrees",
                    "sample_ms", "active_min_angle_deg", "static_confirm_frames",
                    "static_max_gap_ms", "static_max_angle_deg", "core_radius",
                    "center_search_radius")
        expected_motion = set(integers) | {'roi', 'template_offset', 'ring_radii',
                                          'visible_score', 'reference_score', 'reference_margin'}
        if set(motion) != expected_motion:
            raise ValueError('invalid motion parameter keys')
        if any(type(motion[k]) is not int or motion[k] <= 0 for k in integers):
            raise ValueError("invalid motion integer parameters")
        if not (motion["static_confirm_frames"] >= 3
                and motion["white_min"] < 255 and motion["channel_spread_max"] <= 255
                and motion["period_degrees"] == 180 and motion["period_degrees"] % motion["angle_step"] == 0
                and motion['angle_step'] <= motion['active_min_angle_deg'] < motion['period_degrees']/2
                and motion['active_min_angle_deg'] % motion['angle_step'] == 0
                and (motion['static_confirm_frames']-1)*motion['sample_ms'] < timing['page_timeout_ms']
                and motion['sample_ms'] <= motion['static_max_gap_ms'] < timing['page_timeout_ms']
                and motion['static_max_angle_deg'] < motion['active_min_angle_deg']):
            raise ValueError("invalid motion parameters")
        if any(type(motion[k]) not in (int, float) or not isfinite(motion[k]) or not 0 < motion[k] <= 1
               for k in ('visible_score', 'reference_score', 'reference_margin')):
            raise ValueError('invalid motion reference confidence')
        radii, offset = motion["ring_radii"], motion["template_offset"]
        if len(radii) != 2 or any(type(v) not in (int, float) for v in radii) or not 0 < radii[0] < radii[1] < min(motion_region.width, motion_region.height)/2:
            raise ValueError("invalid motion ring")
        if motion['core_radius'] >= radii[0]:
            raise ValueError('motion core overlaps rotating ring')
        if motion['center_search_radius'] + radii[1] + 1 >= min(motion_region.width, motion_region.height)/2:
            raise ValueError('invalid motion center localization')
        if len(offset) != 2 or any(type(v) is not int for v in offset) or not (0 <= offset[0] < motion_region.width and 0 <= offset[1] < motion_region.height):
            raise ValueError("invalid motion template offset")
        geometry = raw["geometry"]
        if (type(geometry['event_card_change_min']) not in (int, float)
                or not isfinite(geometry['event_card_change_min'])
                or not 0 < geometry['event_card_change_min'] <= 255
                or 'event_card_change' not in regions or 'event_rank_entry' not in regions):
            raise ValueError('invalid event card transition settings')
        if (type(geometry['event_card_stable_max']) not in (int, float)
                or not isfinite(geometry['event_card_stable_max'])
                or not 0 <= geometry['event_card_stable_max'] < geometry['event_card_change_min']):
            raise ValueError('invalid event card stability threshold')
        for name in ("node_triangle_max_dx", "node_triangle_max_dy", "loot_selection_half_width", "shop_entry_max_dx", "shop_entry_max_dy", "shop_result_padding"):
            if type(geometry[name]) is not int or geometry[name] <= 0:
                raise ValueError(f"invalid geometry: {name}")
        hero_region = geometry["hero_selection_offset"]
        if len(hero_region) != 4 or any(type(v) is not int for v in hero_region) or min(hero_region[2:]) <= 0:
            raise ValueError("invalid hero selection offset")
        profile = raw['lineups'][lineup.key]
        heroes = tuple(profile['heroes'])
        if tuple((h["key"], h["ticket"], h["attribute"]) for h in heroes) != lineup.heroes:
            raise ValueError("invalid initial hero order")
        if (tuple(profile['rank_priority']) != lineup.rank_priority
                or profile['recruitment_policy'] != lineup.recruitment_policy.value
                or profile['departure_hero'] != lineup.departure_hero):
            raise ValueError('invalid lineup rank priority or recruitment policy')
        stage = raw["stage_selection"]
        stage_keys = {"open_offset", "drag_start", "drag_steps", "step_distance_px", "step_ms", "frame_check_ms", "settle_ms", "max_drags", "timeout_ms", "lobby_scenes", "top_entry_max_y", "lobby_ambiguity_margin"}
        if set(stage) != stage_keys:
            raise ValueError("invalid stage selection settings")
        top_y = stage["top_entry_max_y"]
        if type(top_y) is not int or not regions["stage_list"].y <= top_y < regions["stage_list"].bottom:
            raise ValueError("invalid top stage entry boundary")
        margin = stage["lobby_ambiguity_margin"]
        if type(margin) not in (int, float) or not isfinite(margin) or not 0 < margin < 1:
            raise ValueError("invalid lobby stage ambiguity margin")
        if any(scenes[key].region != regions["lobby_stage_text"] for key in lobby_scenes.values()):
            raise ValueError("lobby stage scenes must use the lobby stage text region")
        for name in ("drag_steps", "step_distance_px", "step_ms", "frame_check_ms", "settle_ms", "max_drags", "timeout_ms"):
            if type(stage[name]) is not int or stage[name] <= 0:
                raise ValueError(f"invalid stage selection {name}")
        if not (stage["step_ms"] <= 100 and stage["drag_steps"]*stage["step_ms"] <= 2000
                and stage["drag_steps"] <= 50 and stage["drag_steps"]*stage["step_distance_px"] <= 10000
                and stage["frame_check_ms"] <= 100
                and stage["max_drags"] <= 30 and stage["timeout_ms"] <= 120000):
            raise ValueError("stage selection bounds exceeded")
        values = stage["drag_start"]
        if not isinstance(values, list) or len(values) != 2 or any(type(v) not in (int, float) or not isfinite(v) or not 0.05 <= v <= 0.95 for v in values):
            raise ValueError("invalid stage selection drag_start")
        offset = stage["open_offset"]
        if not isinstance(offset, list) or len(offset) != 2 or any(type(v) is not int for v in offset):
            raise ValueError("invalid stage selector opening offset")
        start_scene = scenes["start_exploration"]
        if start_scene.action is None:
            raise ValueError("start exploration requires a click anchor")
        width, height = sizes[start_scene.template_key]
        anchor_x, anchor_y = start_scene.action
        # The entry lies above the button, within its horizontal extent and one
        # button height above its top. These limits derive from loaded pixels.
        if not (0 < offset[0] < width*(1-anchor_x)
                and -height*(1+anchor_y) <= offset[1] < -height*anchor_y):
            raise ValueError("stage selector opening offset is outside the button-relative limits")
        triangle = raw['text_triangle']
        bounds = {'edge_distance_px': (1, 3), 'edge_samples': (7, 21),
                  'edge_margin_fraction': (.05, .25), 'pixel_contrast_min': (1, 30),
                  'edge_contrast_min': (1, 40), 'edge_support_min': (.8, 1),
                  'interior_brightness_min': (180, 255)}
        if (not isinstance(triangle, dict) or set(triangle) != set(bounds)
                or any(type(triangle[k]) not in (int, float) or not isfinite(triangle[k])
                       or not low <= triangle[k] <= high for k, (low, high) in bounds.items())
                or any(type(triangle[k]) is not int for k in ('edge_distance_px', 'edge_samples'))
                or triangle['edge_contrast_min'] < triangle['pixel_contrast_min']):
            raise ValueError('invalid text triangle settings')
        recognition = _loot_recognition(raw["loot_recognition"], sizes, scenes, expected_loot)
        events = _event_recognition(raw["event_recognition"], sizes, scenes, regions)
        daily_reset, shared_paths = load_daily_reset(config)
        paths.update(shared_paths)
        for key, scene in daily_reset.scenes.items():
            scenes[key] = Scene(scene.template_key, scene.region, scene.threshold, scene.structure, scene.action)
        settings = ChaosSettings(scenes, timing, geometry, motion, heroes, loot, regions, stage, tuple(pending), triangle, recognition, events, daily_reset, target_stage, lineup)
        values = {f.name: getattr(config, f.name) for f in fields(AppConfig)}
        values["template_paths"] = paths
        values['component_layouts'] = {**components, **daily_reset.component_layouts}
        return ChaosConfig(**values, chaos=settings)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        raise ConfigError([f"Chaos configuration: {exc}"]) from exc


def _event_recognition(value, sizes, scenes, regions):
    integers = {"feature_width": (32, 256), "feature_height": (8, 64), "feature_shift": (0, 2),
                "foreground_min": (1, 254), "channel_spread_max": (1, 255),
                "local_padding": (1, 40), "top_k": (1, 10)}
    if not isinstance(value, dict) or set(value) != set(integers) | {"layouts", "templates", "readiness"}:
        raise ValueError("invalid event recognition settings")
    for key, (low, high) in integers.items():
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f"invalid event recognition {key}")
    readiness = value['readiness']
    if (not isinstance(readiness, dict) or set(readiness) != {
            'contrast_kernel', 'feature_shift', 'alignment_tolerance',
            'similarity_min', 'contrast_ratio_min', 'feature_change_max'}):
        raise ValueError('invalid event readiness settings')
    for key, low, high in (('contrast_kernel', 3, 31), ('feature_shift', 0, 6), ('alignment_tolerance', 0, 2)):
        number = readiness[key]
        if type(number) is not int or not low <= number <= high or (key == 'contrast_kernel' and number % 2 == 0):
            raise ValueError(f'invalid event readiness {key}')
    for key in ('similarity_min', 'contrast_ratio_min', 'feature_change_max'):
        number = readiness[key]
        if type(number) not in (int, float) or not isfinite(number) or not 0 < number < 1:
            raise ValueError(f'invalid event readiness {key}')
    layouts, bindings = value['layouts'], value['templates']
    if not isinstance(layouts, dict) or set(layouts) != {2, 3}:
        raise ValueError("event index requires two- and three-option layouts")
    area = regions['event_options']
    for count, slots in layouts.items():
        if not isinstance(slots, list) or len(slots) != count:
            raise ValueError("invalid event title slots")
        previous = area.x
        for slot in slots:
            r = _rect(slot, area.right, area.bottom)
            if r.x < previous or r.y < area.y or r.width != slots[0][2] or r.height != slots[0][3]:
                raise ValueError("event title slots must be aligned, ordered and inside event options")
            previous = r.right
    if not isinstance(bindings, dict) or set(bindings) != EVENT_IDENTITIES or not EVENT_IDENTITIES <= set(scenes):
        raise ValueError("event index must cover every registered entry event exactly once")
    for key, spec in bindings.items():
        if scenes[key].region != area or scenes[key].action is None:
            raise ValueError(f"indexed event must use the event-options scene and a click anchor: {key}")
        if not isinstance(spec, dict) or set(spec) != {'layout', 'origin', 'titles', 'weights'}:
            raise ValueError(f"invalid event index binding: {key}")
        count = spec['layout']
        if type(count) is not int or count not in layouts:
            raise ValueError(f"invalid event layout: {key}")
        origin, titles, weights = spec['origin'], spec['titles'], spec['weights']
        if (not isinstance(origin, list) or len(origin) != 2 or any(type(n) is not int for n in origin)
                or not isinstance(titles, list) or len(titles) != count
                or not isinstance(weights, list) or len(weights) != count
                or any(type(n) not in (int, float) or not isfinite(n) or not 0 <= n <= 1 for n in weights) or not any(n > 0 for n in weights)):
            raise ValueError(f"invalid event title metadata: {key}")
        w, h = sizes[scenes[key].template_key]
        if not (area.x <= origin[0] <= area.right-w and area.y <= origin[1] <= area.bottom-h):
            raise ValueError(f"event index reference outside scene: {key}")
        for title, slot in zip(titles, layouts[count]):
            r = _rect(title, w, h)
            if not (slot[0] <= origin[0]+r.x < origin[0]+r.right <= slot[0]+slot[2]
                    and slot[1] <= origin[1]+r.y < origin[1]+r.bottom <= slot[1]+slot[3]):
                raise ValueError(f"event title outside its layout slot: {key}")
    return value


def _loot_recognition(value, sizes, scenes, identities):
    integers = {"top_k": (1, 10), "feature_size": (16, 64), "feature_shift": (0, 2),
                "local_padding": (1, 20), "anchor_y_tolerance": (1, 20),
                "layout_tolerance": (1, 20), "name_foreground_min": (1, 254),
                "name_channel_spread_max": (1, 255), "name_extra_pixels_max": (0, 50)}
    scores = {"ambiguity_margin", "icon_threshold", "icon_structure", "name_threshold", "name_structure"}
    expected = set(integers) | scores | {"card_offset", "icon_band", "name_band", "anchor_spacing", "template_regions"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("invalid loot recognition fields")
    for key, (low, high) in integers.items():
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f"invalid loot recognition {key}")
    for key in scores:
        if type(value[key]) not in (int, float) or not isfinite(value[key]) or not 0 < value[key] <= 1:
            raise ValueError(f"invalid loot recognition {key}")
    card = value["card_offset"]
    if (not isinstance(card, list) or len(card) != 4 or any(type(v) is not int for v in card)
            or not 0 < card[2] <= 600 or not 0 < card[3] <= 1000
            or not -600 <= card[0] <= 0 or not -1100 <= card[1] < 0):
        raise ValueError("invalid loot card offset")
    for key in ("icon_band", "name_band"):
        _rect(value[key], card[2], card[3])
    spacing = value["anchor_spacing"]
    if (not isinstance(spacing, list) or len(spacing) != 2 or any(type(v) is not int for v in spacing)
            or not card[2] < spacing[0] < spacing[1] < 1000):
        raise ValueError("invalid loot anchor spacing")
    if set(value["template_regions"]) != identities:
        raise ValueError("loot subregions must cover every identity")
    for key, parts in value["template_regions"].items():
        if set(parts) != {"icon", "name", "origin"}:
            raise ValueError(f"invalid loot subregions: {key}")
        width, height = sizes[scenes[key].template_key]
        origin = parts["origin"]
        if not isinstance(origin, list) or len(origin) != 2 or any(type(v) is not int for v in origin):
            raise ValueError(f"invalid loot origin: {key}")
        for part in ("icon", "name"):
            r = _rect(parts[part], width, height)
            band = Rect(*value[part + "_band"])
            x, y = origin[0]-card[0]+r.x, origin[1]-card[1]+r.y
            if not (band.x <= x < x+r.width <= band.right and band.y <= y < y+r.height <= band.bottom):
                raise ValueError(f"loot {part} outside card band: {key}")
    return value


# Explicit canonical three-choice identities; priorities remain in YAML.
LOOT_IDENTITIES: frozenset[str] = frozenset("""
loot_summon_mark loot_ominous_crystal loot_old_sword loot_lightning loot_dimension_core
loot_priest_ring loot_star_shard loot_confession loot_sky_gate loot_painting_guard
loot_painting_fire loot_painting_mez loot_automaton_heart loot_sun_shield loot_dimension_pouch
loot_painting_panorama loot_painting_sanctuary loot_painting_blind_war loot_statue_two_faces
loot_tablet_persecution loot_chalice_offering loot_chalice_redemption loot_ancient_key
loot_world_tree_leaf loot_star_book loot_beast_scale loot_statue_gazer loot_tablet_prosperity
loot_painting_cosmos loot_painting_apostasy loot_chalice_silent loot_painting_silent_agreement
loot_painting_forgotten_legend loot_statue_sleeping_priest loot_tablet_dusk_stars
loot_priest_unfinished_record loot_old_shield loot_twin_star_ring loot_dice_hero
loot_tablet_sleeping_forest
loot_moon_charm loot_shrine_fragment loot_statue_star_child loot_tablet_comet_prophecy
loot_tablet_silver_oath loot_chalice_dark_communion loot_painting_star_mineral loot_time_sand
loot_painting_ruins_below loot_dice_fate loot_transcendence_crystal loot_statue_whisperer
loot_statue_lost_light loot_statue_guardian_judgment loot_statue_prayer loot_chalice_seal_hymn
loot_tablet_comet_altar loot_starlight_bottle loot_dimension_crystal_pillar loot_dice_revival
loot_dead_compass loot_tablet_flame_record loot_guardian_claw
loot_alchemy_flask loot_sealed_letter loot_temple_investigation_badge
loot_painting_suppression_night
loot_beacon_ring loot_summoning_ritual_disk loot_painting_flame_hymn
loot_tablet_spirit_king_invitation loot_statue_blind_saint loot_painting_road_to_sky_gate
loot_watcher_breastplate loot_glowing_stone loot_statue_watcher
""".split())

# Shared dependencies only; each lineup declares its own additional scenes.
COMMON_SCENES: frozenset[str] = frozenset("""
home_battle battle_title exploration_card exploration_back intro_enter lobby_research
start_exploration stage_one contract contract_selected supply_confirm ticket_thief
title_thief ticket_soul title_soul ticket_warrior title_warrior ticket_knight
title_knight ticket_all title_all depart_title depart_confirm depart_missing
filter_light filter_dark filter_icon_dark
filter_icon_light filter_icon_water filter_water_selected filter_dark_selected filter_light_selected
filter_icon_fire filter_fire_selected
hero_ivanna recruited_ivanna
selected_name_ivanna hero_xiaochun recruited_xiaochun selected_name_xiaochun
hero_teacher hero_selected_border recruit_confirm recruit_enter
map_hero map_title_outer map_title_center map_title_deep node_normal node_event
node_rest node_shop node_boss node_elite node_loot node_triangle
node_enter prepare_normal prepare_elite prepare_boss start_battle auto_static
levelup levelup_close victory failed continue_exploration elite_choose_reward
story_skip story_skip_prompt story_skip_confirm
boss_choose_reward elite_claim_complete boss_claim_complete unclaimed_prompt unclaimed_confirm journey_result
journey_close camp_rank camp_rank_done rank_title rank_confirm rank_value_1
rank_value_2 rank_value_3 rank_value_4 rank_value_5 rank_close camp_leave
event_torch event_mimic
text_continue
loot_selected loot_confirm loot_reroll_anchor supply_get_loot supply_loot_done supply_leave investment
investment_insufficient_item health_restore health_restore_unavailable shop_health_entry purchase_health_restore health_restore_done
investment_entry purchase_title purchase_item purchase_confirm investment_done shop_leave
shop_leave_prompt shop_leave_confirm network_connection_abnormal network_retry
stage_three stage_01 stage_02 stage_03 stage_04 stage_05 stage_selected stage_confirm
event_trap prepare_event event_reward_close
event_tablet event_tablet_continue
event_vestment event_vestment_no_loot event_vestment_with_loot event_golem event_giant_eye event_prison
event_cage event_altar
event_sword event_cairn event_mushroom event_rank_enter
event_library event_flame event_torn_map event_torn_map_path event_mural event_blood_shelter event_passage event_observatory
event_backpack event_spirit_door event_ghost
event_erased_records event_fairy_dance event_blue_flame_altar event_broken_mask event_ancient_corridor
event_angry_inscription event_ancient_stele event_blood_meteorite event_windy_cliff
event_forked_path event_starlight_flower event_grieving_woman
event_withered_garden event_prayer_room event_golden_patterns event_clear_pond event_clear_pond_low_health
event_warning_sign event_warning_sign_low_health event_mysterious_footprints event_old_armory event_old_armory_left event_blood_circle
event_grimoire event_broken_candlestick event_red_phantom event_broken_cage
event_sharp_teeth event_collapsed_stairs event_bone_throne
event_recruit_enter event_battle_rank_done
""".split()) | LOOT_IDENTITIES

KNOWN_SCENES = COMMON_SCENES | frozenset().union(*(lineup.scenes for lineup in LINEUPS))
