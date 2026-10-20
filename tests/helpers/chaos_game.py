"""A game-side model: inventory/page changes require valid clicks, not flow calls."""
from dataclasses import replace
from pathlib import Path

from e7auto.core.types import Point, Rect, Size
from e7auto.core.ports import DisplayGeometry
from e7auto.features.chaos.contracts import AutoState, Detection, MapView, Node
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.configuration.loader import load_config
from tests.helpers.paths import ROOT
from tests.helpers.services import FakeClock, FakeWindowService, FakeInput, FakeLogger, FakeOverlay, FakeRuntimeEnvironment
from e7auto.runtime.dependencies import AutomationDependencies


def chaos_config(lineup_id='dark_twins'):
    config = with_chaos_config(load_config(ROOT / "config/internal.yaml", template_profile="chaos"), lineup_id=lineup_id)
    return replace(config, executable_path=Path(r"D:\Games\Fake\Game.exe"), process_name="Game.exe", window_title="Game Window")


class ChaosGame:
    def __init__(self, config, *, network_after=None, wrong_selection=False, unknown_loot=False,
                 multi_knights=False, fail_battle=False, endless_battle=False, stage_one=True,
                 event_type="event_torch", event_reward_key="loot_summon_mark", event_intro_pages=0,
                 vestment_has_loot=False):
        self.config = config;self.clock = FakeClock();self.state = "home"
        self.routes = ("normal", "rest", "loot", "event", "shop", "elite", "boss", "boss", "normal", "boss")
        self.node_index = 0;self.floor = 1;self.kind = None;self.recruited = set();self.hero = None
        self.selection = False;self.filter_open = False;self.attributes = set();self.profession = False
        self.ticket = None;self.ranks = dict.fromkeys((h['key'] for h in config.chaos.heroes), 1)
        self.auto = False;self.swapped = False;self.battle_started = 0;self.actions = [];self.completed = 0
        self.network_after = network_after;self.network_remaining = 0;self.network_injected = False
        self.wrong_selection = wrong_selection;self.unknown_loot = unknown_loot;self.multi_knights = multi_knights
        self.fail_battle = fail_battle;self.endless_battle = endless_battle;self.stage_one = stage_one
        self.unlocked_stages = {1, 2, 3, 4, 5}
        self.on_capture = None;self.on_click = None;self.captures = 0;self.closed = False
        self.points = {};self.names = list(config.chaos.scenes)
        self.stage_scrolled = True;self.stage_chosen_one = False
        self.pointer_held = False;self.pointer_start = None;self.on_pointer = None
        self.event_type=event_type;self.event_reward_key=event_reward_key
        self.event_intro_pages=event_intro_pages;self.event_intro_remaining=event_intro_pages
        self.vestment_has_loot=vestment_has_loot

    def close(self):self.closed = True
    @property
    def stage_one(self):return self.current_stage == 1
    @stage_one.setter
    def stage_one(self, value):self.current_stage = 1 if value else 3
    @property
    def stage_chosen_one(self):return self.chosen_stage == 1
    @stage_chosen_one.setter
    def stage_chosen_one(self, value):self.chosen_stage = 1 if value else 3
    def reset_temporal(self):self.invalidate_event_recognition()
    def begin_event_recognition(self):self._event_candidate = None
    def end_event_recognition(self):self.invalidate_event_recognition();return {}
    def invalidate_event_recognition(self):self._event_candidate = None
    def close_event_recognition(self):self.invalidate_event_recognition()
    def event_choice(self, frame):
        previous = getattr(self, '_event_candidate', None)
        if previous is not None:
            self._event_candidate = self.control(frame, previous.key)
        else:
            self._event_candidate = next((hit for key in self.config.chaos.event_recognition['templates']
                                          if (hit := self.control(frame, key)) is not None), None)
        return self._event_candidate
    def begin_loot_recognition(self):pass
    def end_loot_recognition(self):return {}
    def close_loot_recognition(self):pass
    def prepare_frame(self, frame):return frame

    def rank_result(self, frame):
        if frame['state'] != 'rank_result':return None
        before, after = frame['rank_before'], frame['rank_after']
        if before is None or after is None:return None
        if all(f'rank_value_{n}' in frame['visible'] for n in (before, after)):
            return before, after
        return None

    def capture_client(self, _window, _bounds):
        self.captures += 1
        if self.on_capture:self.on_capture(self)
        if self.state == "battle" and not self.endless_battle and self.clock.now-self.battle_started >= 2:
            self.state = "failed" if self.fail_battle else "event_reward" if self.kind == "event" else "journey" if self.kind == "boss" and self.floor == 3 else "settlement"
            if self.state == "event_reward" and self.event_type == "event_blood_shelter":
                self.state = "event_rank_offer"
        visible = self._visible()
        return dict(visible=frozenset(visible), state=self.state, floor=self.floor, kind=self.kind,
                    rank_before=getattr(self, 'rank_before', None), rank_after=self.ranks.get(self.hero),
                    selected=self.selection, hero=self.hero, teacher="teacher" in self.recruited,
                    swapped=self.swapped, network=self.network_remaining > 0, auto=self.auto,
                    stage_chosen_one=self.stage_chosen_one, chosen_stage=self.chosen_stage)

    def _visible(self):
        s = self.state
        if s == "home":return ["home_battle"]
        if s == "battle_menu":return ["battle_title", "exploration_card"]
        if s == "intro":return ["intro_enter", "exploration_back"]
        if s == "hall":
            scene = self.config.chaos.stage_selection['lobby_scenes'].get(self.current_stage)
            return ["lobby_research", "exploration_back", "start_exploration"]+([scene] if scene else [])
        if s == "stage_panel":return ["stage_confirm", "stage_selected"]+([] if self.stage_scrolled else [f"stage_{n:02d}" for n in sorted(self.unlocked_stages)])
        if s == "contract":return ["contract", "supply_confirm"]+(["contract_selected"] if self.selection else [])
        if s == "recruit_cards":return ["recruit_enter"]+["ticket_"+k for k in ("thief", "soul", "warrior", "knight")]+["recruited_teacher_initial" if k == 'teacher' else "recruited_"+k for k in self.recruited]
        if s == "recruit":
            keys = ["title_"+self.ticket, "filter_light" if self.filter_open else "filter_dark", "recruit_confirm"]
            if self.filter_open:
                keys += ["filter_"+a+"_selected" if a in self.attributes else "filter_icon_"+a for a in ("dark", "fire", "water", "light")]
                keys += ["filter_thief_selected" if self.profession else "filter_icon_thief_filter"]
            else:keys += ["hero_"+self.hero]+(["hero_selected_border", "selected_name_"+self.hero] if self.selection else [])
            return keys
        if s in ("map", "node"):return ["node_enter"] if s == "node" else []
        if s == "prepare":
            return ["prepare_"+self.kind, "start_battle"]+(["bench_arowell"] if self.swapped else ["bench_teacher"] if "teacher" in self.recruited else [])+(["bench_selected", "formation_knight", "replace_button"] if self.selection else [])
        if s == "battle":return []
        if s == "failed":return ["failed"]
        if s == "journey":return ["journey_result", "journey_close"]
        if s == "settlement":return ["victory", "continue_exploration"]+([self.kind+"_choose_reward"] if self.kind != "normal" else [])
        if s == "loot":return ["loot_confirm"]+(["loot_selected"] if self.selection else [])
        if s == "claimed":return [self.kind+"_claim_complete", "continue_exploration"]+(["ticket_all" if self.floor == 2 else "ticket_thief"] if self.kind == "boss" else [])
        if s == "teacher_done":return ["recruited_teacher", "continue_exploration"]
        if s == "unclaimed":return ["unclaimed_prompt", "unclaimed_confirm"]
        if s == "camp":return ["camp_rank", "camp_leave"]
        if s == "rank":return ["rank_title", "rank_confirm"]+["hero_"+h for h in self.config.chaos.lineup.rank_priority]+(["hero_selected_border", "selected_name_"+self.hero] if self.selection else [])
        if s == "rank_result":
            if self.kind == "event" and self.event_type == "event_blood_shelter":
                return ["event_battle_rank_done", "continue_exploration"]
            return [f"rank_value_{self.rank_before}", f"rank_value_{self.ranks[self.hero]}", "rank_close"]
        if s == "camp_done":return ["camp_rank_done", "camp_leave"]
        if s == "event":return [self.event_type]
        if s == "event_intro":return ["text_continue"]
        if s == "event_ticket":return ["event_tablet_continue", "event_recruit_enter"]
        if s == "event_unclaimed":return ["unclaimed_prompt","unclaimed_confirm"]
        if s == "event_rank_offer":return ["event_rank_enter", "continue_exploration"]
        if s in ("cairn_hp", "cairn_reward"):return ["event_reward_close"]
        if s == "vestment_subchoice":return ["event_vestment_with_loot" if self.vestment_has_loot else "event_vestment_no_loot"]
        if s == "armory_subchoice":return ["event_old_armory_left"]
        if s == "event_reward":return ["event_reward_close"]+([self.event_reward_key] if self.event_reward_key else [])
        if s == "text":return ["text_continue"]
        if s == "supply":return ["supply_get_loot"]
        if s == "supply_done":return ["supply_loot_done", "supply_leave"]
        if s == "shop":return ["investment", "investment_entry", "shop_leave"]
        if s == "purchase":return ["purchase_title", "purchase_item", "purchase_confirm"]
        if s == "shop_done":return ["investment_done", "health_restore_unavailable", "shop_leave"]
        if s == "shop_prompt":return ["shop_leave_prompt", "shop_leave_confirm"]
        raise AssertionError(s)

    def _hit(self, key, frame):
        index = self.names.index(key) if key in self.names else 150
        bounds = Rect(100+index*9, 700, 30, 20)
        if key == "start_exploration":bounds = Rect(1662,1135,612,133)
        elif key in {f"stage_{n:02d}" for n in range(1,6)}:bounds = Rect(1024,96+(int(key[-2:])-1)*163,110,73)
        elif key == "stage_selected":bounds = Rect(995,55+(frame['chosen_stage']-1)*163,1199,146)
        elif key.startswith("hero_") and key != "hero_selected_border":bounds = Rect(700, 210, 250, 45)
        elif key == "hero_selected_border":bounds = Rect(650, 180, 560, 173)
        elif key == "contract":bounds = Rect(180, 260, 200, 265)
        elif key == "contract_selected":bounds = Rect(48, 215, 482, 795)
        elif key in ("loot_ominous_crystal", "loot_old_sword", "loot_lightning"):
            index = ("loot_ominous_crystal", "loot_old_sword", "loot_lightning").index(key);bounds = Rect(420+580*index, 280, 320, 370)
        elif key == "loot_selected":bounds = Rect(934 if self.wrong_selection else 354, 196, 460, 710)
        elif key == "investment":bounds = Rect(798, 211, 155, 249)
        elif key in ("investment_entry", "investment_done"):bounds = Rect(738, 510, 278, 84)
        elif key in ("health_restore", "health_restore_unavailable"):bounds = Rect(1095, 660, 255, 250)
        elif key == "formation_knight":bounds = Rect(1065, 246, 57, 55)
        elif key == "replace_button":bounds = Rect(1166, 422, 52, 62)
        # Same hero row may show a different identity on different pages, exactly as in game.
        anchor = Point(bounds.x+bounds.width//2, bounds.y+bounds.height//2)
        self.points[anchor] = key
        return Detection(key, 1., bounds, anchor)

    def control(self, frame, key, region=None):
        if frame["network"]:return None
        if key not in frame["visible"] and not (frame["state"] == "loot" and key in ("loot_ominous_crystal", "loot_old_sword", "loot_lightning")):
            return None
        hit = self._hit(key, frame)
        if region is not None and not (region.x <= hit.bounds.x and hit.bounds.right <= region.right and region.y <= hit.bounds.y and hit.bounds.bottom <= region.bottom):return None
        return hit

    def controls(self, frame, key, region=None):
        hit = self.control(frame, key, region)
        if hit is None:return ()
        return (hit, replace(hit, bounds=Rect(1500, 500, 57, 55))) if key == "formation_knight" and self.multi_knights else (hit,)

    def stage_highlights(self, frame, entry):
        return self.controls(frame, 'stage_selected')

    def event_card_snapshot(self, frame):
        return frame['state']

    def event_card_stable(self, frame, previous):
        return frame['state'] == previous

    def event_card_changed(self, frame, previous):
        return frame['state']=='cairn_reward' and frame['state']!=previous

    def map_view(self, frame):
        if frame["state"] != "map" or frame["network"]:return None
        kind = self.routes[self.node_index]
        hit = Detection("node_"+kind, 1., Rect(500, 400, 100, 100), Point(550, 450));self.points[hit.anchor] = hit.key
        return MapView(self.floor, (Node(kind, hit),))

    def loot_cards(self, frame):
        if frame["state"] != "loot" or self.unknown_loot or frame["network"]:return None
        return tuple(self._hit(k, frame) for k in ("loot_ominous_crystal", "loot_old_sword", "loot_lightning"))

    def auto_state(self, frame, _now):
        if frame["network"] or frame["state"] != "battle":return AutoState.UNKNOWN
        return AutoState.ROTATING if self.auto else AutoState.STATIC

    def begin_auto_recognition(self, now):pass
    def auto_anchor(self):
        r = self.config.chaos.auto_motion['roi']
        return Point(r[0]+r[2]//2, r[1]+r[3]//2)
    def auto_recognition_summary(self):return {}

    def network_connection_error(self, frame):
        return Detection("network_connection_abnormal",1.,Rect(0,0,1,1),Point(1,1)) if frame["network"] else None

    def network_retry(self, frame):
        return Detection("network_retry",1.,Rect(0,0,1,1),Point(1,1)) if frame["network"] else None

    def click(self, _window, point):
        if self.network_remaining:
            assert not self.pointer_held, 'network retry before drag release'
            self.actions.append("network_retry");self.clock.sleep(10);self.network_remaining-=1;return
        offset=self.config.chaos.stage_selection['open_offset']
        if self.state=='hall' and point==Point(1968+offset[0],1201+offset[1]):key='open_stage'
        elif point == Point(2044,62):key = "enable_auto"
        else:key = self.points[point]
        self.actions.append(key)
        if key == "home_battle":self.state="battle_menu"
        elif key == "exploration_card":self.state="intro"
        elif key == "intro_enter":self.state="hall"
        elif key == "open_stage":self.state='stage_panel'
        elif key in {f"stage_{n:02d}" for n in range(1,6)}:
            assert int(key[-2:]) in self.unlocked_stages
            self.chosen_stage=int(key[-2:])
        elif key == "stage_confirm":
            assert self.chosen_stage in self.unlocked_stages
            self.current_stage=self.chosen_stage;self.state='hall'
        elif key == "start_exploration":
            self.state="contract";self.node_index=0;self.floor=1;self.selection=False;self.recruited=set();self.auto=False;self.swapped=False;self.ranks=dict.fromkeys((h['key'] for h in self.config.chaos.heroes),1)
        elif key == "contract":self.selection=True
        elif key == "supply_confirm":self.state="recruit_cards";self.selection=False
        elif key.startswith("ticket_"):
            self.ticket=key.removeprefix("ticket_");self.hero=({h['ticket']:h['key'] for h in self.config.chaos.heroes}|{'all':'teacher'})[self.ticket];self.state="recruit";self.attributes=set();self.profession=False;self.filter_open=False;self.selection=False
        elif key in ("filter_light","filter_dark"):self.filter_open=not self.filter_open
        elif key == "filter_icon_thief_filter":self.profession=True
        elif key.startswith("filter_icon_"):self.attributes.add(key.removeprefix("filter_icon_"))
        elif key.startswith("filter_") and key.endswith("_selected"):self.attributes.discard(key[7:-9])
        elif key.startswith("hero_"):self.hero=key.removeprefix("hero_");self.selection=True
        elif key == "recruit_confirm":
            assert self.selection;assert self.hero not in self.recruited;self.recruited.add(self.hero);self.state="teacher_done" if self.ticket=="all" else "recruit_cards";self.selection=False
        elif key == "recruit_enter":assert len(self.recruited)==4;self.state="map"
        elif key.startswith("node_") and key!="node_enter":self.kind=key.removeprefix("node_");self.state="node"
        elif key == "node_enter":
            self.selection=False
            self.state="prepare" if self.kind in ("normal","elite","boss") else {"rest":"camp","loot":"supply","event":"event","shop":"shop"}[self.kind]
            if self.kind=='event' and self.event_intro_pages:
                self.event_intro_remaining=self.event_intro_pages;self.state='event_intro'
        elif key == "start_battle":self.state="battle";self.battle_started=self.clock.now
        elif key == "enable_auto":self.auto=True
        elif key.endswith("_choose_reward"):self.state="loot";self.selection=False
        elif key.startswith("loot_") and key != "loot_confirm":self.selection=True
        elif key == "loot_confirm":assert self.selection;self.state="supply_done" if self.kind=="loot" else "claimed";self.selection=False
        elif key == "continue_exploration":
            if self.kind=='event' and self.event_type=='event_blood_shelter':self.state='text'
            elif self.kind=="boss" and self.state!="teacher_done":self.state="unclaimed"
            else:self._map_next()
        elif key == "unclaimed_confirm":
            if self.state=='event_unclaimed':self.state='text'
            else:self._map_next()
        elif key == "camp_rank":self.state="rank";self.selection=False
        elif key == "rank_confirm":
            assert self.selection
            self.rank_before=self.ranks[self.hero]
            self.ranks[self.hero]=min(5,self.rank_before+(2 if self.event_type=='event_grimoire' and self.kind=='event' else 1))
            self.state="rank_result"
        elif key == "rank_close":self.state="text" if self.kind=='event' else "camp_done"
        elif key=='text_continue' and self.state=='event_intro':
            self.event_intro_remaining-=1
            if self.event_intro_remaining==0:self.state='event'
        elif key in ("camp_leave","supply_leave","shop_leave_confirm","text_continue"):self._map_next()
        elif key in ("event_torch","event_mimic","event_golem","event_altar","event_library","event_flame","event_torn_map","event_torn_map_path","event_mural","event_observatory","event_sharp_teeth","event_collapsed_stairs"):self.state="text"
        elif key in ("event_trap","event_giant_eye","event_prison","event_cage","event_sword","event_blood_shelter","event_broken_cage"):self.state="prepare"
        elif key in ("event_mushroom", "event_ghost", "event_starlight_flower", "event_grimoire", "event_bone_throne"):self.state="event_rank_offer"
        elif key == "event_rank_enter":self.state="rank";self.selection=False
        elif key in ("event_cairn", "event_spirit_door", "event_blue_flame_altar"):self.state="cairn_hp"
        elif key in ("event_backpack", "event_erased_records", "event_broken_mask"):self.state="event_reward"
        elif key in ("event_fairy_dance", "event_ancient_corridor"):self.state="text"
        elif key in ("event_angry_inscription", "event_blood_meteorite", "event_windy_cliff"):self.state="text"
        elif key == "event_ancient_stele":self.state="event_reward"
        elif key in ("event_forked_path", "event_grieving_woman"):self.state="cairn_hp"
        elif key in ("event_withered_garden", "event_prayer_room", "event_golden_patterns"):self.state="text"
        elif key in ("event_clear_pond", "event_clear_pond_low_health"):self.state="event_reward"
        elif key in ("event_warning_sign", "event_warning_sign_low_health", "event_mysterious_footprints", "event_blood_circle", "event_broken_candlestick"):self.state="text"
        elif key == "event_old_armory":self.armory_phase=1;self.state="prepare"
        elif key == "event_old_armory_left":
            assert self.state == 'armory_subchoice' and self.armory_phase == 1
            self.armory_phase=2;self.state='prepare'
        elif key in ("event_tablet", "event_passage"):self.state="event_ticket"
        elif key == "event_tablet_continue":self.state="event_unclaimed"
        elif key == "event_vestment":self.state="vestment_subchoice"
        elif key == "event_vestment_no_loot":
            assert self.state=='vestment_subchoice' and not self.vestment_has_loot
            self.state='text'
        elif key == "event_vestment_with_loot":
            assert self.state=='vestment_subchoice' and self.vestment_has_loot
            self.state='text'
        elif key == "event_reward_close":
            if self.event_type == 'event_old_armory' and self.armory_phase == 1:self.state='armory_subchoice'
            else:self.state="cairn_reward" if self.state=='cairn_hp' else "text"
        elif key == "supply_get_loot":self.state="loot"
        elif key == "investment_entry":self.state="purchase"
        elif key == "purchase_confirm":assert self.state=="purchase";self.state="shop_done"
        elif key == "shop_leave":self.state="shop_prompt"
        elif key == "bench_teacher":self.selection=True
        elif key == "replace_button":assert self.selection;self.swapped=True;self.selection=False
        elif key == "journey_close":self.state="hall";self.completed+=1
        elif key == "exploration_back":self.state="intro" if self.state=="hall" else "battle_menu"
        elif key == "battle_title":self.state="home"
        else:raise AssertionError(key)
        if key==self.network_after and not self.network_injected:self.network_remaining=4;self.network_injected=True
        if self.on_click:self.on_click(self,key)

    def _map_next(self):
        if self.kind=="boss":self.floor+=1
        self.node_index+=1;self.state="map";self.selection=False

    def pointer_down(self, _window, point):
        assert not self.pointer_held and self.state=='stage_panel'
        self.pointer_held=True;self.pointer_start=point;self.actions.append('pointer_down')
        if self.on_pointer:self.on_pointer(self,'pointer_down',point)

    def pointer_move(self, _window, point, *, allow_outside_client=False):
        assert self.pointer_held
        self.actions.append('pointer_move')
        if self.state=='stage_panel' and point.y-self.pointer_start.y>=200:self.stage_scrolled=False
        if self.on_pointer:self.on_pointer(self,'pointer_move',point)

    def pointer_up(self, _window, point):
        assert self.pointer_held
        self.pointer_held=False;self.actions.append('pointer_up')
        if self.on_pointer:self.on_pointer(self,'pointer_up',point)

    def dependencies(self):
        windows=FakeWindowService(display_geometry=DisplayGeometry(1,r"\\.\DISPLAY1",Rect(0,0,3120,2080),Size(3120,2080),96))
        return AutomationDependencies(windows=windows,capture=self,inputs=self,overlay=FakeOverlay(),vision=self,clock=self.clock,logger=FakeLogger(),runtime=FakeRuntimeEnvironment())
