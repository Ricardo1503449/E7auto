"""Route the first fully verified, temporally confirmed event once."""
from collections.abc import Callable

from e7auto.core.domain import StopReason
from e7auto.runtime.stop_control import StopExecution
from ..contracts import Detection
from ..configuration import EVENT_IDENTITIES
from .context import EventContext
from . import torch, mimic, trap, tablet, vestment, golem, giant_eye, prison, cage, altar, sword, cairn, mushroom
from . import library, flame, torn_map, mural, blood_shelter, passage, observatory
from . import backpack, spirit_door, ghost
from . import erased_records, fairy_dance, blue_flame_altar, broken_mask, ancient_corridor
from . import angry_inscription, ancient_stele, blood_meteorite, windy_cliff, forked_path, starlight_flower, grieving_woman
from . import withered_garden, prayer_room, golden_patterns, clear_pond
from . import warning_sign, mysterious_footprints, old_armory, blood_circle
from . import grimoire, broken_candlestick, red_phantom, broken_cage
from . import sharp_teeth, collapsed_stairs
from . import bone_throne


HANDLERS: dict[str, Callable[[EventContext, Detection], None]] = {
    "event_torch": torch.run,
    "event_mimic": mimic.run,
    "event_trap": trap.run,
    "event_tablet": tablet.run,
    "event_vestment": vestment.run,
    "event_golem": golem.run,
    "event_giant_eye": giant_eye.run,
    "event_prison": prison.run,
    "event_cage": cage.run,
    "event_altar": altar.run,
    "event_sword": sword.run,
    "event_cairn": cairn.run,
    "event_mushroom": mushroom.run,
    "event_library": library.run,
    "event_flame": flame.run,
    "event_torn_map": torn_map.run,
    "event_torn_map_path": torn_map.run,
    "event_mural": mural.run,
    "event_blood_shelter": blood_shelter.run,
    "event_passage": passage.run,
    "event_observatory": observatory.run,
    "event_backpack": backpack.run,
    "event_spirit_door": spirit_door.run,
    "event_ghost": ghost.run,
    "event_erased_records": erased_records.run,
    "event_fairy_dance": fairy_dance.run,
    "event_blue_flame_altar": blue_flame_altar.run,
    "event_broken_mask": broken_mask.run,
    "event_ancient_corridor": ancient_corridor.run,
    "event_angry_inscription": angry_inscription.run,
    "event_ancient_stele": ancient_stele.run,
    "event_blood_meteorite": blood_meteorite.run,
    "event_windy_cliff": windy_cliff.run,
    "event_forked_path": forked_path.run,
    "event_starlight_flower": starlight_flower.run,
    "event_grieving_woman": grieving_woman.run,
    "event_withered_garden": withered_garden.run,
    "event_prayer_room": prayer_room.run,
    "event_golden_patterns": golden_patterns.run,
    "event_clear_pond": clear_pond.run,
    "event_clear_pond_low_health": clear_pond.run,
    "event_warning_sign": warning_sign.run,
    "event_warning_sign_low_health": warning_sign.run,
    "event_mysterious_footprints": mysterious_footprints.run,
    "event_old_armory": old_armory.run,
    "event_blood_circle": blood_circle.run,
    "event_grimoire": grimoire.run,
    "event_broken_candlestick": broken_candlestick.run,
    "event_red_phantom": red_phantom.run,
    "event_broken_cage": broken_cage.run,
    "event_sharp_teeth": sharp_teeth.run,
    "event_collapsed_stairs": collapsed_stairs.run,
    "event_bone_throne": bone_throne.run,
}

if set(HANDLERS) != EVENT_IDENTITIES:
    raise ValueError("Event handlers and indexed entry identities must agree")


def identify(context: EventContext, frame: object) -> Detection | None:
    return context.vision.event_choice(frame)


def dispatch(context: EventContext, event: Detection, summary: dict | None = None) -> None:
    handler = HANDLERS.get(event.key)
    if handler is None:
        raise StopExecution(StopReason.CHAOS_UNKNOWN_CONTENT, f"unregistered event: {event.key}")
    metrics = {key: summary[key] for key in ('event_frames', 'event_candidates_checked',
                                           'event_cache_checks', 'event_recognition_ms')
               if summary is not None and key in summary}
    context.runtime.deps.logger.event("chaos_event_dispatched", event_key=event.key, **metrics)
    handler(context, event)
