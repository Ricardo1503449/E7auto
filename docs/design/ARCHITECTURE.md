# Architecture

Chaos supports two fixed lineups, selected in the settings page and captured before
the worker starts. `features/chaos/lineups.py` defines their identities and contracts;
`chaos.yaml` binds each lineup's initial recruits, rank priority and later-recruitment
policy. Each Lineup declares its own `scenes` independently of its typed
`recruitment_policy` (`none`, `teacher_for_arowell` or `restore_shadow_rose`). Configuration combines
`COMMON_SCENES`, the selected lineup's scene set and the selected lobby stage before
resolving template images; the known-scene pool is the union of registered lineup
declarations, common scenes and all five lobby stage bindings. It does not derive resource dependencies from the
recruitment policy or subtract one team's dependencies to construct another team.
Runtime voucher and replacement entry points consult only the recruitment policy,
while retaining their page, floor and pending-state guards.
The budget lineup never requires dark-twins/replacement-only resources. Every round
initializes its roster and priorities from the selected lineup, while exploration,
confirmation, input and recovery mechanics remain shared. Only the dark-twins lineup
enables second-floor Teacher recruitment; replacement is required only while Arowell
remains in the roster. The budget lineup conditionally restores a departed Shadow Rose
at the unused second-floor voucher, confirming its distinct all-class completion
before recording rank 1. Both lineups explicitly declare a departure target. Shared
all-class voucher scenes do not activate a recruitment policy by themselves.
Initial Teacher completion has its own scene; the previous special-ticket result
template and its selection guards remain unchanged. Release checks validate both
lineup configurations. Camp skips when its priority list is fully maxed; mandatory
rank-up events keep their existing no-target stop guard.

## Source organization

`app.py` starts the application. `bootstrap.py` selects the feature and assembles production services.
`ui/` owns Qt presentation, thread adaptation and signals. The worker calls
`create_production_session` from its `run()` method; WGC imports remain inside that factory.

`ui/preparation.py` captures a frozen RunRequest and validates configuration/resources on
a cancellable preparation thread. No game service is created in that phase. Once preparation
has fully exited, the GUI thread configures its overlay and starts AutomationWorker. Closing
during preparation requests cancellation and defers window destruction until cleanup; a
cancelled result can never start automation. Preparation errors are shown in the feature page;
configuration rejections retain their best-effort startup log in the background.
AutomationWorker emits its terminal result even when logging fails. MainWindow keeps its
run guard and disabled controls until QThread.finished plus a nonblocking successful wait(0),
so result delivery cannot authorize restart during native thread teardown.

Run-end notifications are presentation-only: MainWindow retains the terminal snapshot
until the worker thread has fully exited, then `ui/notifications.py` formats and
deduplicates one notification per run. Normal completion, expected resource/strategy
stops and failures notify; manual stops and cancelled preparation never notify.
Uncancelled preparation failure also reports once. No result card or activation action
is added. `ui/preferences.py` atomically saves the enabled-by-default switch under
`state/ui_preferences.json`; failed writes revert the switch and show a settings error.
`platform/notifications.py` registers E7auto's own per-user AppUserModelId display
identity and sends native ToastGeneric messages using `platform/toast_runtime.py`.
The latter scopes Windows Runtime ABI pointers and HSTRINGs without importing PyWinRT
or introducing a new binary dependency. A process-lifetime no-op COM activator keeps
notification selection from launching/focusing the app; there is no LocalServer32 or
autostart registration. The COM class registration is revoked at process shutdown;
the per-user display identity remains for Windows-owned history. Toast messages allow the default
sound and obey system notification settings. No tray icon, window message hook, expiry
cleanup, Hide or history-clear call remains. Disabling notifications affects future
sends; closing the window does not remove delivered records. Notification failures
cannot block run cleanup.

`ui/desktop_actions.py` performs directory creation and file/browser default-handler launch off the GUI
thread through `platform/desktop.py`, which initializes and releases COM STA on that thread.
Pending window close cancels queued actions and waits asynchronously for in-flight calls;
threads are never forcibly terminated. `ui/styles.py` centralizes the main stylesheet and
shared design values. `ui/pages/feature.py` supplies presentation-only cards, forms and limit
validation without coupling feature business implementations. Card background caches use
physical pixel sizes and include DPR in the cache key while preserving logical cover/crop.

All application-owned dialogs derive from ui/widgets.AppDialog. Explicit opening focuses
the unadorned dialog container and disables implicit default buttons; it does not remove
child focus policies or focused-button styling. Tab/Shift+Tab still navigate controls,
Enter activates only a focused button, Space retains native button behavior, and Escape
uses the dialog's existing reject/closing guards. System-driven show events do not reset
the user's chosen focus. Future dialogs use this same base rather than raw QDialog.

AppDialog.open uses nonblocking, non-modal window presentation. MainWindow owns an
ui/widgets.AppDialogHost; dialogs discover it through their parent chain and register
on show, without feature-specific visibility signals. The host disables only appBody
and drives the shared backdrop; title-bar movement, minimize/maximize and close remain
available, while page navigation and automation starts stay blocked. Move/resize and
restore recenter dialogs using their frame geometry, with one coalesced follow-up after
native geometry changes. Hiding or destroying the last dialog restores the previous
content-enabled state; overlapping dialogs keep the content locked. Closing the main
window still respects each dialog's reject guard and the existing installer handoff.
Dialogs must use the shared open path rather than QDialog.open/exec or WindowModal.

MainWindow owns one ui/updates.UpdateController and injects it into Settings. It owns the
asynchronous UpdateChecker, a one-shot scheduler, and atomic state/update_status.json cache;
Settings and the sidebar subscribe to the same availability signal. Only explicit manual
requests emit dialog results; a manual request can join an automatic request already in flight.
First show starts a one-second delay once, successful checks remain fresh for six hours,
and automatic failures back off through 30 minutes, one/two/four/six hours. GitHub rate-limit
headers set a persisted lower bound for both automatic and manual requests. Network failures
preserve confirmed releases. Availability is recomputed against the running version instead
of caching a badge flag, and viewing notes never acknowledges an update. Window close cancels
the controller and queued settings actions; late results cannot change state or open dialogs.
Reminder dots are Qt logical-pixel painting, anchored to the gear and update title, with
accessible descriptions and no tooltips. No automation or recognition layer depends on them.

The same successful release response supplies an immutable ReleaseAsset (version, exact
Windows installer name/URL, size and GitHub SHA-256); this execution metadata is not restored
from the reminder cache. Manual update dialogs bind their displayed notes to that asset.
MainWindow owns ui/update_install.UpdateInstaller, which streams a bounded, cancellable
download into state/updates/<version>-<job ID>, verifies size/digest, and starts a worker
for the platform/update_install Windows handoff. Only explicit user action starts it.
HTTPS redirects are restricted to GitHub asset hosts; no auth token, TLS bypass or new
dependency is used. Updating shares the existing busy guard and normal asynchronous close.
Setup launch failure leaves the app open; successful launch closes only after worker teardown.

The existing installer owns replacement and user-data preservation. Its explicit update mode
waits at most 60 seconds for the running mutex without terminating a process, then follows
the normal registered-directory/manifest/downgrade checks. On success it starts the installed
EXE with a validated update job ID. The application waits for the setup mutex before taking
its own lifetime guard; normal starts still refuse active setup immediately. Once the new
window is shown, a filesystem-only cleanup thread removes that job's package only if its
version, filename, size and digest match. Installer logs and request metadata remain; normal
runtime data and unrelated files are never cleanup targets.

`core/` defines geometric types, service ports, shared observations and the existing runtime snapshot.
`runtime/` owns the guarded window/capture/input services, active clock, network recovery,
entry navigation, stop lock, snapshot publication and session cleanup. `RuntimeSession` receives
an explicit flow factory and `RunPolicy`; it does not choose or import feature implementations.
`RuntimeContext` carries one run's shared state. Shop cache invalidation is an injected recovery
callback, not a network-service dependency on shop fields.

`runtime/daily_reset.py` owns opt-in date-change recovery. It receives a runtime,
shared settings, named observation callback and home detector; it never imports a
feature or navigates back into one. `wait_for_ready` prioritizes the date title over
the caller's ready page, requires stable evidence and observes two active seconds
after the first stable ready page without restarting that timer on repeated hits.
It returns READY or, after closing date, confirming sign-in and optionally closing
login rewards and buff effects, HOME. Each screenshot checks both optional popup
titles before accepting home. Conflicting titles or partial popups block background-home
evidence. Buff effects can follow sign-in directly or a closed login reward; a buff
cannot transition back to login reward. Sign-in is submitted once; only the same stable
date/reward/buff close can retry within the configured
attempt and time limits. Network recovery clears temporal evidence without resetting
progress or input budgets; capture, input, active time and stop guards remain shared.

`configuration/daily_reset.py` loads six common/daily_reset templates and
their internal.yaml settings only for explicit consumers. Chaos adapts these shared
scenes to its existing vision contract; shop/penguin load only their explicit common
keys, so missing date-reset resources do not affect their startup. The five resources
were placed in common immediately at the user's request, ahead of a second consumer.
The buff title uses its popup-header ROI, excluding the small home-screen buff label.
`buff_effect_close` aliases the existing login-reward close image, ROI and thresholds;
the runtime maintains separate close signatures, attempts and deadlines for each popup.
Chaos calls recovery while returning from success or defeat settlement, rechecks
before stage/start and final lobby-exit actions, and keeps counts and reentry navigation
in its own flow. Remaining rounds reenter from confirmed home and verify the target
stage; the last successful round stays at home after recovery. No in-round global
date interceptor or fixed reset-time schedule is installed.

`features/shop/` owns the shop flow, inventory/purchase rules, refresh/balance strategies,
scroll navigation and shop image measurements. `features/penguin/` owns the independent exchange
flow, control/price recognition and feature resource configuration. Both flows compose a runtime;
neither inherits or imports the other. Their recognition protocols and business results live in
`contracts.py` beside the corresponding feature.

ShopFlow explicitly composes InventoryScanner and RefreshController with one typed
ShopState. Refresh receives only the inventory component and entry/exit callbacks;
neither component receives the whole flow. The flow owns navigation and orchestration,
and network recovery invalidates the same shared balance/pending-scan state.
ChaosFlow similarly composes StageSelection and HeroRecruitment through narrow
guarded callbacks. Battle, round and recovery state remain owned by ChaosFlow.
Recruitment filtering observes the required toggles and next action together over
one stable window. It clears only the historical dark/water/light non-targets,
then sets the target attribute and optional profession. One toggle is submitted
at a time; its expected effect must be observed before another input is allowed.
The final settings and close button share one three-frame confirmation. Unknown
toggle pixels cannot establish an off state. No extra profession or fire cleanup
is added, and profession-disabled calls do not load or query profession controls.
Teacher replacement jointly confirms the selected bench, unique formation knight
and associated arrow. Forfeiture jointly confirms its prompt and button; a partial
prompt blocks the background map. Both actions consume the last stable button
through guarded input and retain their destination confirmation.
Camp rank results and the close button are observed in one loop. Numeric evidence
still records progress once after three frames, independently of close visibility;
the existing close-phase timeout starts once at that point. Closing additionally
requires three joint result/button frames. Recovery resets consecutive evidence
without replaying submission or erasing confirmed progress. Event reward-card
closing and battle-rank result paths remain independent and unchanged.

`features/chaos/` owns the independent three-floor exploration flow, scene/resource bindings,
round state, dynamic node/card recognition and continuous auto-battle motion evidence. It
composes `RuntimeContext` and its active clock, network recovery and guarded input. The
configuration profile loads only its active resource keys and the two shared network keys;
scene aliases share pixels without merging page guards or outcome checks. The UI worker
selects Chaos explicitly, with separate round counters and completion state.
The selected target stage (1-5, default 1) is captured in ChaosSettings before
the worker starts. Every round matches only that stage's digit template at the
configured 0.93 color and 0.8 structure gates. Other lobby digits are neither
loaded nor compared; there is no score-ranking or confidence-gap requirement.
A missing target digit enters stage selection instead of authorizing exploration.
Stage switching keeps
the bounded drag, target-row highlight and post-submit lobby confirmation;
the left difficulty-number check has been removed. A stable first row near
the list top establishes coverage of stages 1-5; an absent selectable target
then stops without further dragging. ROI, thresholds and top-row boundary
remain in YAML; recognition uses the shared native-pixel FrameView contract.
Lobby readiness remains an independent three-frame check and keeps the two-active-second
observation after settlement. The subsequent stage check jointly verifies the date guard,
research marker, start button and the selected stage digit over three fresh frames.
Its last start button is handed off once, including after a stage switch; no second
start-confirmation loop follows. New captures, feature input, round initialization,
network recovery and cleanup invalidate the handoff, whose capture count, recovery
generation, transform and target must still match when consumed. It stores no screenshot.
The five registered lobby digits use the YAML `lobby_stage_text` ROI [1720,1025,220,90].
Only the startup-selected digit is a runtime dependency; missing or damaged other
lobby digits do not block that target. The full five-entry binding map remains
validated, and release verification checks both lineups with all five targets.
The old `stage_bar` is removed. Selector input retains the start-anchor offset [250,-125],
with only the offset scaled on non-reference clients. Pre-input checks use client bounds,
the button search region, its horizontal extent and the band one button height above
its top; offset limits derive from loaded button dimensions and the configured anchor.
Both client sizes share these business rules while retaining separate matching backends.
Stage-highlight matching derives a vertical band from the current frame's target
entry and the loaded highlight template height. The band covers every complete
border that could contain the entry, within the configured list ROI. Scaled frames
retain original captured pixels and global client coordinates when cropping this band. Each
successful selection observation returns its already-detected confirmation button;
the stable wait still requires three new frames and submits the final frame's button.
Both client sizes search and deduplicate borders before the selection controller
checks containment and uniqueness. Containment does not prune matcher proposals
or candidates before deduplication.
The selector panel jointly observes its target entry, containing unique highlight
and submit button. An already-selected target can submit from the panel's first
three-frame window; a newly clicked target requires fresh joint confirmation.
Current-frame entry and button detections are reused for highlight association.
Date-change popups are monitored at lobby boundaries only, as confirmed by the
user. Panel waits, selection and drags do not probe the date title; post-submit
lobby confirmation resumes its existing date priority and recovery handling.
Chaos startup adapts its home-battle and battle-menu detections to the shared
navigation observation contract. It uses shared startup navigation and bounded
wake, then the same configured five-second entry wait and five-second
destination/main recheck as shop/penguin, with at most two entry clicks.
Chaos map and other page waits retain their separate 15-second setting. Only stable home evidence permits
another click; late battle-menu arrival completes entry without replaying it.
FAILED recognition is restricted to submitted battles awaiting success; shared
navigation and all post-success pages do not probe it.

FrameView region/crop operations require complete in-view regions by default.
Dynamic search intersections and candidate padding explicitly opt into clipping;
empty or insufficient regions never provide full-template evidence. Fixed content
reads remain strict, and nested views retain the full-client transform and origin.

Map recognition first verifies the hero marker, unique floor title and selectable
triangles. Each triangle then limits the seven node-type searches to the envelope
of the existing pairing tolerances plus complete template extents. Windows are
clipped to configured scene bounds, and overlapping windows deduplicate the same
physical node before the unchanged unique-pairing check. All node windows use the
map ROI already normalized for triangle detection, avoiding resize-phase changes
as triangles bounce. Detection bounds and input anchors remain baseline-global.
The three-frame stability rule and one-use stable-map handoff are unchanged.

Chaos events have one handler module per event under `features/chaos/events/`: `torch.py`,
`mimic.py`, `trap.py`, `tablet.py`, `vestment.py`, `golem.py`, `giant_eye.py`, `prison.py`, `cage.py`, `altar.py`, `sword.py`, `cairn.py`, `mushroom.py`, `library.py`, `flame.py`, `torn_map.py`, `mural.py`, `blood_shelter.py`, `passage.py`, `observatory.py`, `backpack.py`, `spirit_door.py`, `ghost.py`, `erased_records.py`, `fairy_dance.py`, `blue_flame_altar.py`, `broken_mask.py`, `ancient_corridor.py`, `angry_inscription.py`, `ancient_stele.py`, `blood_meteorite.py`, `windy_cliff.py`, `forked_path.py`, `starlight_flower.py`, `grieving_woman.py`, `withered_garden.py`, `prayer_room.py`, `golden_patterns.py`, `clear_pond.py`, `warning_sign.py`, `mysterious_footprints.py`, `old_armory.py`, `blood_circle.py`, `grimoire.py`, `broken_candlestick.py`, `red_phantom.py`, `broken_cage.py`, `sharp_teeth.py`, `collapsed_stairs.py` and `bone_throne.py`. `entry.py` advances shared pre-option text, while
`dispatcher.py` explicitly maps the first fully verified, three-frame-stable event
candidate to its handler. The approved policy no longer requires an exhaustive
catalog uniqueness scan. After node entry, events alone wait four active seconds;
both entry and final text handling check the text triangle before options/map.
Each miss returns to fresh triangle-first polling, not a permanent mode switch.
After dispatch, the chosen handler owns every remaining business step and confirms the
original-floor map; no mid-event redispatch occurs. Multi-choice events such as vestment
register only their first identity globally; later subchoices are recognized inside that handler.
Vestment accepts both available and insufficient-loot second-stage templates locally,
and both target the left red-light option without another global dispatch. `EventContext` supplies narrow callbacks
and the existing guarded runtime, without importing `ChaosFlow` or platform implementations.
The shared `_fight` capability returns the requested battle result; reward and
continue inputs stay with the caller. Elite and first-two-floor boss reward
entry clicks consume the returned three-frame-confirmed Detection immediately,
using its last anchor via guarded `_tap`; they do not start another `_act`
confirmation. The expected actionable scene is checked, and the configured
post-click loot delay and destination confirmation remain. Normal, elite and first-two-floor boss
battles probe the level-up title first every `battle_signal_poll_ms` (1000ms),
then VICTORY only on a title miss, using the same network/FAILED-guarded frame.
A popup can obscure the transient VICTORY banner without network recovery;
its title independently starts observation. Auto confirmation uses this same
throttled probe while retaining its motion sampling. One matching signal starts the
`battle_result_settle_ms` observation window (3000ms), not a blind delay.
Fresh 100ms samples jointly confirm the level-up title and close control over
three frames, then submit the latest close position immediately. A partial or
fading popup blocks the underlying result. Closing happens once, keeps the
300ms click buffer and proceeds directly to fresh stable result confirmation.
Without a popup the full window must elapse before result confirmation. Network
recovery clears consecutive evidence and pauses active time; the window never
restarts. A recovery-only probe also accepts popup/result evidence until a
missed VICTORY can be bypassed, including intervening transition frames. Boss
story handling can likewise hand off its positive result evidence. The original
battle deadline bounds every phase. Event battles and the final boss retain
their own targets and `settlement_poll_ms` cadence (5000ms).
Phase logs distinguish first signal, popup visible/complete/stable, close
submission and final result; one summary records active/wall time, captures and
separate level-up/VICTORY matching counts and costs without per-frame log output.
The level-up search region is [998,201,334,577], mapped from the user-selected
crop; the close region, template pixels and thresholds are unchanged.

All battles that probe level-up retain the popup guard through their next stable
page. `_wait_battle_transition` serves normal battles' continue-button confirmation
and original-floor map return, plus elite and first-two-floor boss loot entry.
The same title/close templates take priority on each fresh frame; partial or
residual popup evidence blocks destination confirmation and underlying input.
A per-battle submitted-close flag spans settlement and these transitions, survives
recovery, and resets for the next fight. A late popup requires joint three-frame
confirmation and keeps the 300ms close buffer. The destination is checked first
after it disappears. Only when that transition has submitted a late close and
the same continue/reward control is newly stable for three frames may it retry
the control once, using the latest anchor and original configured click buffer.
Recovery alone never authorizes a replay. Each original 15-second active wait
keeps its deadline across matching, closing and retry; normal battles preserve
separate continue-button and map waits. A confirmed original-floor map retains
the existing one-use handoff to the next node. Transition close/resume logs
supplement the existing settlement and loot summaries. Supply, event battles and
the final boss keep their existing paths; both client-size branches use the same
flow without changing recognition pixels or thresholds.

Automatic-battle recognition shares `auto_motion.AutoIconState` across client sizes.
The user confirmed that switching auto OFF returns the ring to its fixed reference
pose. Each visible frame is compared with the original OFF ring in polar coordinates:
centre score >=0.75, reference-ring score >=0.75 and non-adjacent angular peak margin
>=0.04 are required. An absolute offset >=10 degrees modulo 180 confirms ON from
that frame, including a quarter turn; no prior-frame ring, rotation window, net
travel, direction count or direction ratio remains. An unreliable frame is UNKNOWN,
not OFF. A near-reference pose can be a passing ON angle and therefore cannot click
immediately: OFF requires three consecutive fresh observations within +/-5 degrees
of the fixed reference, spaced by 100-250ms. The third qualified observation
authorizes the existing single click without a second outer confirmation. Angles
between the OFF band and the 10-degree ON threshold, visibility loss, bad ring
evidence, time reversal, position changes and recovery clear the OFF counter.
Only the counter and its timestamps are retained; no angle history or duration
threshold remains. Other controls keep their original three-frame confirmation.

Dispatch still uses the complete client transform. Baseline frames use the original
fixed centre and click anchor, without scaled candidates or centre search.
Non-reference frames use existing phase candidates and configured local centre
search on each current frame; the ring and optional click share that location.
There is no three-frame centre acquisition or separate baseline motion class.
Geometry changes rebuild the detector; recovered waits reset evidence without
resetting diagnostics. Result-first probing, guarded single clicks, the 15-second
wait and all battle/stop/network protections remain shared and unchanged.
The summary reports reference angle/score/margin, reference rejection count,
first visibility/active times, centre offset and the remaining OFF evidence.
Boss battles additionally look for story skip/prompt signals before settlement,
including during auto-mode confirmation. A skip match authorizes one immediate
guarded click; the prompt and reused confirm button require fresh stable evidence.
Consecutive story segments remain in a bounded loop until a positive result signal
appears. Confirmation dismissal requires three fresh absent frames; only a stable
unchanged control permits a bounded retry. Active-time story and original battle
deadlines, network recovery and stop guards remain in force. Once settlement starts,
the original result-only wait resumes and never probes story controls again.
Three consecutive fresh FAILED matches unwind the current node or event to the
round owner through BattleDefeated. A miss or network recovery breaks the evidence;
suspected failure temporarily blocks result and auto-button actions. The round
owner clicks the confirmed FAILED location once, requires the journey result,
and uses the same guarded journey close/lobby confirmation as normal completion.
Only then does it start a fresh RoundState without incrementing rounds_completed.
A missing destination, manual stop or window error stops without repeating dismissal/close.
The battle-only failure guard is cleared on story victory evidence, stable battle
results, and all battle exits (including exceptions).
Ordinary-node settlement stays in `flow.py`, while direct-loot settlement belongs
to each battle-event handler.
Directly granted event loot is closed from its confirmed event context and close prompt;
its item identity is neither recognized nor logged. Item templates are canonical three-choice
cards only. Ordinary window-coordinate adaptation remains independent of card presentation.
Event reward/HP-card handlers and event rank-result closing share
`EventContext.close_reward_cards`, implemented in `events/rewards.py`. A bounded
state machine requires stable static card information and a close prompt before input, then verifies
the next distinct stable card or dismissal to text/the original-floor map. Only a
still-visible, stable unchanged card permits a bounded close retry. Input dispatch
alone never marks the event closed. Future card handlers use the same capability;
event choices, reward submission and rank submission are never retried by it.
The YAML `event_card_change` ROI covers lower static information, excluding
animated hero portraits. Both the stability and next-card comparisons use this
ROI; portrait motion alone cannot reset confirmation or authorize another page.

A caller may supply a next-page detector to reward closing. Only that stable
destination can confirm dismissal in this mode; text or a background map cannot.
The armory owns this detector and both battles in old_armory.py: select the middle
option, fight and close to its second choice, select the left option, fight and
close to text/map. Its second choice is a local scene, never a global event identity.

The torch, prayer-room, mysterious-footprints and broken-candlestick handlers explicitly
call events/experience.py's finish_with_optional_levelup after their experience choice.
This shared helper checks the level-up title/close before text or map on every frame,
sends one stable close and blocks underlying actions until the popup disappears.
Its close state belongs to each invocation and survives network recovery; original-floor
map confirmation completes it. It reuses EventContext.wait/tap and preserves each event's
wait label. The common text/map finishing method has no popup flag or state.
The user verified that event battles award no experience, so their handlers and battle
settlement do not opt into this helper.
Reward/rank cards retain their
separate confirmed closing helper. Golden-pattern salutes update every saved hero
rank once after result-text evidence, capped at five, without the rank selection UI.

Camp and ordinary event rank results use five masked icon/value templates. All
ranks, including five, retain the rank icon and digit; the Max suffix is not
required. ChaosVision.rank_result reads unique before/after values from
two disjoint YAML regions in the same frame; no arrow or combination template is
required. The shared rank flow accepts a step count (default one), requires three
fresh observations equal to (saved_before, min(5, saved_before + steps)), then
stores the confirmed after value once. Grimoire requests two steps and uses the
existing event-card close helper. Battle reward cards lack this panel and retain
the separate done-card confirmation for single-rank rewards; multi-rank requests
for that layout fail before input. Original combination PNGs remain registered
but are no longer runtime scene/resource dependencies.

`event_recognition.py` builds masked title-ink indexes from unchanged event PNGs.
YAML specifies each event's two/three-option layout, template-relative title crops,
reference alignment and title weights. Both layouts are queried, without a hard
option-count gate. Each layout has one persistent worker that verifies its own
index order; the first complete match cancels the other search. This scheduling
policy is shared by baseline and non-reference clients; only pixel verification
dispatches by the full-client transform. Baseline discovery prepares one read-only
half-size proposal image per frame on the owner thread; cached rechecks do not
prepare it, and no captured pixels persist across recognition calls. The
owner drains running work before updating state or dispatching input. Index ranks
alone never authorize a click. Later fresh frames locally reverify only the cached
candidate; a failed recheck returns no observation, breaking temporal stability.
Recovery, text, input and session boundaries invalidate the candidate. Cached
template variants and native cropped FrameViews are read-only; workers share a synchronized
byte-bounded template cache and never capture, dispatch input or write logs.
Session cleanup closes both recognition executors. New entry events must update
the explicit registry, scene and index metadata together; missing metadata fails
configuration loading. Calibration records are not runtime dependencies.

Three-choice recognition lives in `features/chaos/loot.py`. A shared reroll-text
anchor locates three card-local regions. At load time, unchanged identity PNGs
provide masked icon thumbnails and full-name crops; YAML owns the subregions and
relative offsets. A vectorized icon ranking proposes five candidates, with local
full-catalog fallback only when they fail. Verified icon plus complete name ends a
clear search early; near-ranked verified alternatives fail closed. Name matching
uses a narrow window, then one expansion to that card's name band. In-memory 3x3
smoothing tolerates resampling without changing template assets.

The matcher caches immutable template preprocessing within its instance. Loot
anchor recognition fingerprints the entire native search ROI of every fresh
frame with SHA-256. Only identical scene parameters, template identity, shape,
dtype and pixel digest permit reuse of the previous complete search result.
Any change runs the original full-region search, including the extra-anchor
check. This cache retains no captured arrays and follows the existing recognition
invalidation lifecycle; `anchor_cache_hits` is included in the aggregate summary.

The flow explicitly begins and ends one loot recognition session. Consecutive
fresh captures still need three agreeing results. Later captures reverify each
cached identity's icon and name, rather than repeating discovery. Recovery, missing
anchors, layout changes and session exit invalidate evidence; a changed cached
card fails its current frame and is rediscovered on the next capture. Session
cleanup runs before any selection input and on exceptions. One aggregate
`chaos_loot_recognition` log records outcome, timings and work counts; detectors
do not write per-frame or per-template logs.

The three cards within one frame run on a lazily created, persistent executor
with at most three workers. Template pixels, masks and index arrays are read-only.
The owner thread first locates anchors and crops native card views with global origins.
Workers receive a slot's candidate and
pixels, and return evidence plus private counters; only the owner merges results
in left-to-right order and updates candidates/log totals after the batch completes.
Workers never capture, click, access Qt/WGC or write logs. A worker error cancels
pending work, drains running work and invalidates the whole batch. Flow cleanup
closes the executor on normal completion, manual stop and failures; ordinary recognition
session exit clears candidates while retaining idle workers for the next reward.
`cards_wall_ms` measures elapsed batch time, while index/icon/name timings sum
overlapping worker durations and must not be interpreted as elapsed run time.

Elite/boss reward entry uses timing.loot_entry_settle_ms (1000ms by default);
supply loot entry independently uses timing.supply_loot_entry_settle_ms (3000ms).
Each value is the total post-click settle delay, replacing the ordinary action delay. The existing guarded delay keeps network/window/stop checks active;
recognition starts with a fresh frame afterwards. No animation classifier or index
ranking changes are involved, and cached evidence still needs three confirmations.

`vision/` provides array matching, ROI adaptation, shared glyph matching and network recognition.
Shop and penguin detectors compose these components, retaining independent color masks, thresholds
and amount rules. `resources/` validates catalogs and reads template pixels. `configuration/`
separates data models, existing YAML schema names and loading; its file format is unchanged.
`platform/` implements Windows services; `logging/` owns runtime logs and stop snapshots.

The configuration loader delegates game, templates, coordinates, inventory, scroll,
timing, refresh and vision parsing plus cross-field validation to explicit helpers;
error accumulation, validation order and feature resource isolation are unchanged.
Matching keeps BGR channel views until cropping the requested ROI, then makes only
that ROI contiguous. Full color/structure validation rejects color misses before
computing structure. Digit matching caches immutable template masks/dilations/counts,
and prepares each query's nine shifted masks once, preserving scoring and tie breaks.

Before importing PyWinRT, the WGC adapter calls `platform/native_runtime.py` to pin and validate
the Qt-compatible MSVC runtime. This performs no Qt import or COM initialization. The old
WinRT-private MSVCP140 must not win the process-wide dependency lookup. An already-loaded
outdated runtime is rejected rather than unloaded. Source tests now cover Qt and WGC in the same
process; release builds normalize root and nested CRT copies before publication.

Project scripts and tests use the canonical modules. Legacy root modules and the old automation
package have been removed after migrating callers. `scripts.project.check_layout` also checks
source ownership, forbidden dependencies and deferred WGC imports in the selected disk/index view.
See [development](../development/DEVELOPMENT.md) for extension rules and targeted testing.

## Data and threads

- The Qt main thread owns `MainWindow` and `StatsOverlay` only.
- The window owns its title bar and resize handles. Those controls keep weak back-references and validate the PySide wrapper before accessing the window, avoiding a Python ownership cycle and safely ignoring events after native destruction. Closing a production window retains the existing behavior; deterministic deletion is managed separately by UI test fixtures.
- On the native Windows backend the main window is opaque. `platform/window_frame.py` restores native caption/thick-frame capabilities for DWM transitions and Windows 11 corner clipping, while handling non-client size/hit-test messages to retain the drawn title bar. Qt's frameless hint remains only for its client geometry bookkeeping. Maximized clients are clipped to the selected monitor's physical work area, and resize hit testing uses the window's current DPI. Native initialization runs once per HWND, so show/restore does not interrupt transitions by reapplying frame styles. Non-native test backends retain the previous Qt-only frame; overlays and result dialogs retain their existing transparency. Global animation preferences are not modified.
- `AutomationWorker` runs the session on a `QThread` and publishes frozen `RuntimeSnapshot` objects through Qt signals.
- `SnapshotPublisher` is the single counter source and emits the final snapshot once. Later mutation attempts are ignored.
- Overlay activity is snapshot state rather than widget inference: startup publishes `已启动`, confirmed shop entry publishes `刷新ing...`, strategy main-screen recovery publishes `转运ing...`, and finalization publishes `已停止`. The red stop button below the top-left collapse button requests a manual stop for every feature. It disables immediately until the next run; no separate instructional row is displayed. The collapsed view has no stop control; users expand the panel first. The overlay is always mouse-interactive and draggable; releasing a drag persists the position. Its top-left `收起` control switches to a draggable circular Logo view, and a click without dragging restores the full panel. Every run starts in the full-panel state. Only a final snapshot exposes the top-right close button so the user can hide the overlay without exiting the application. Starting another run hides the close button and shows the full overlay again.
- `StopController` separates the short stop-state lock from input serialization. Manual stop requests never wait for platform input or logging. Admission checks the stop state while holding the input serialization lock; a request that wins admission rejects the next callback. An already admitted operation may finish, including release-only cleanup.

## Fail-closed flow

1. Main-thread input validation freezes each run request; background configuration/resource validation must succeed before the GUI creates the automation worker and before any game service or input is permitted.
2. Because the observed game process runs at High integrity, the production process must also be genuinely Windows-elevated. The standalone executable embeds a `requireAdministrator` manifest; rejecting UAC prevents startup. Unsandboxed execution without UAC elevation is not equivalent.
3. The worker owns a StopController before its thread starts and passes it into the production session. The main-window slot calls worker.request_stop() directly, touching only the shared lock; a queued worker slot would be blocked by the running automation loop. Pending requests survive session setup, and the session checks them before creating a flow. No global hotkey is registered.
4. The overlay remains directly draggable without pausing automation. Moving it does not alter the WGC game-window capture target or window-message input path. Because WGC captures the designated game window rather than the desktop composition, the overlay does not use display-affinity capture exclusion.
5. The game window must be unique. Before any restore/resize, the window monitor's full `Monitor` rectangle must agree with `EnumDisplaySettings(..., ENUM_CURRENT_SETTINGS)` and meet the configured `2560 x 1440` minimum. A restored background window is left inactive; a minimized window is restored with `SW_SHOWNOACTIVATE` and bounded state polling. Startup never requires or requests foreground ownership. The exact `3120 x 2080` reference mode requests the calibrated `2322 x 1306` client; every other supported mode starts at 60% of full monitor width with the same aspect ratio and is uniformly reduced if its DPI-adjusted complete outer window would exceed monitor height.
6. Every capture/input safety point verifies that the window still exists, is not minimized, has the exact process identity and prepared client rectangle, and remains on the same monitor/mode/DPI. Foreground state is not a production gate; any monitor, mode, geometry, DPI, process, or client change terminates without live resize/recalibration.
7. Entry clicks occur only after stable template evidence. Every click/wheel dispatch rechecks the window under the input lock, maps configured baseline points once while preserving already-client detection anchors, posts the corresponding window message, and logs success only after the platform call returns. Inventory navigation sends six `-120` messages `10 ms` apart (`scroll.interval_ms`) after the top viewport is exhausted. The five configured inter-message sleeps total `50 ms`, down from `500 ms`; this saves `450 ms` of requested sleep per downward navigation, not a measured 90% reduction in total runtime. Individual dispatches do not await game-side processing. Post-wheel settling starts sampling after `100 ms`, checks downsampled pairwise motion every `100 ms`, requires two consecutive observations within `1 px` with phase response at least `0.80`, and fails closed at the configured `1000 ms` maximum (raised from 800 ms on 2026-10-21 to tolerate observed computation overruns). A stable candidate must also pass the calibrated full-resolution total translation/change gate before bottom scanning; stale visually stable frames do not end settling. The last three temporally separated settle frames then seed bottom recognition; an identical three-frame key completes immediately, while a mismatch retains its longest stable suffix and captures only the missing frames. Navigation does not depend on fixed top/bottom anchors.
8. Main-screen detection is defined solely by stable recognition of the shop-entry icon. Its alpha-masked template excludes user-dependent wallpaper; no whole-main-screen template is used. After an entry click, the engine waits for stable shop-refresh recognition. On timeout it jointly checks the shop refresh control and main-screen entry icon; only a stable main-screen result permits another entry click, and the bounded sequence stops after two attempts. Strategy-driven exit first requires stable recognition of the calibrated shop-exit control, clicks its calibrated center once, and then requires stable main-screen icon recognition before any wait or re-entry. Normal completion (`budget_complete` or `refresh_strategy_exhausted`) performs the same guarded shop exit and main-screen confirmation before the final stopped snapshot is published; abnormal and manual stops do not add a new exit click.

## Capture freshness and diagnostics

WGC accepts only an increasing source timestamp whose age at delivery is at most
100 ms by default. Bootstrap wires the window-message adapter's completion
callback to the capture adapter; each successfully posted message advances a QPC
boundary, and subsequent frames must be more than the 50 ms timestamp tolerance
past that boundary. This marks
message delivery to the window queue, not game-side processing. Both adapters
remain on the existing worker; the shared runtime gains no Win32 dependency.

Windows QPC and WGC SystemRelativeTime are converted to the same 100 ns units.
Expired, repeated, negative, excessively future and pre-input frames are rejected
before bitmap copying. Live frames can lead local QPC slightly: up to 50 ms is
allowed before copy, but delivery waits for QPC to catch up if copying did not
already consume the lead. Age is then checked again and is never negative in a
delivered CaptureTiming. The first valid frame has a 2-second startup budget;
subsequent captures use `timing.capture_frame_timeout_ms` (default 1000 ms).
Only an adapter fresh-frame timeout after a successful capture permits recovery.
Each request may rebuild its capture item/pool/session once and then use a fixed
`timing.capture_recovery_timeout_ms` budget (default 3000 ms). Rebuilds reuse the
worker apartment and D3D device; callbacks capture a session-local arrival event.
Source/input watermarks and run statistics survive rebuilding. Cleanup failure
prevents replacement, and final close alone releases the device and apartment.
Rebuild starts are recorded with monotonic time, with a 60000 ms cooldown and
at most three attempts in the preceding 600000 ms. Failed starts count, successful
frames do not reset history, and a new service starts a new history. At either
rate limit the original session instead gets one additional 3000 ms phase.
Startup, cancellation, geometry/identity changes and other native errors do not
authorize recovery. Exhaustion or rebuild failure retains capture_failure.
Draining, frame arrival and catch-up
share each phase's deadline; rejection never resets it or permits an old-frame fallback.
Already fresh queued frames can return without waiting for another arrival.
Runtime supplies an optional wait checkpoint to the capture adapter and releases
it after each call. Startup frame waits use at most 50 ms slices to recheck manual stop and
window/display guards; the same short waits and guards also apply during running
capture, queue draining and after bitmap copy. Their original stop reason
propagates without a capture-failure log.
Startup and in-flight native calls are not forcibly cancelled by this deadline.

CaptureService keeps its array return value. WGC also exposes the immutable
last_capture_timing for its successful call; RuntimeContext stores that optional
CaptureTiming with the matching CachedGameFrame. Missing source timestamps remain
unknown. Source timestamps are not compared to wall time or network-paused time.
Input timestamp failure must not interrupt button release; without a subsequent
valid input timestamp, capture fails rather than using an unknown boundary.

Capture failures include per-call elapsed/wait/bitmap-copy time and separate
before-copy/after-copy expired-frame counts. These reset on every call and do not
add per-frame logs. Normal captures update bounded counters and histogram buckets in memory. Closing
the capture service emits one wgc_capture_summary; capture failures retain detailed
diagnostics. Stop images still save the last recognized capture without taking a
new screenshot, and their logs distinguish cache residence from source-frame age.
These measurements support live tuning without per-frame disk logging.
Recovery start/skip/completion events contain the action, rate-limit reason,
initial/rebuild/recovery durations and outcome. Only a terminal capture failure
increments capture_failures; the summary separately counts attempted, recovered,
failed/interrupted rebuilds, rate-limit hits and successful extended waits.
Successful completion events retain their measured durations and flush before
the next capture or at final close, so logging cannot age a just-validated frame
before delivery. Logging exceptions cannot replace capture or guard errors.
Recovery does not send inputs, pause business clocks or change either native
recognition branch; all three features share the same adapter policy.

## Inventory algorithm

Each refreshed inventory is processed as `stable top loop -> one downward scroll -> stable bottom loop`. Every recognition path uses original client pixels. FrameView carries the full-client transform and crop origin; configuration regions remain in baseline units and are rounded outward once. The 25 explicitly configured composite templates share ComponentMatcher across reference and non-reference clients, as authorized on 2026-10-17; reference clients use the original parent pixels at scale 1:1. Other templates still route identity frames to the preserved baseline OpenCV backend (including event/loot scoring and the original auto glyph), while non-reference frames use the native-scale backend. Non-reference adaptation work must not implicitly replace or change the verified baseline algorithm. Shared-layer changes must assess baseline impact, and intentional baseline behavior changes require explicit user authorization. Non-reference template variants use the exact client scale, bounded quarter-pixel phases, premultiplied colors and effective coverage masks. Padded canvas dimensions never redefine the scale; floating content geometry is rounded only at bounds/input boundaries. The shared lazy LRU budget defaults to 64 MiB, including additional cached preprocessing and reserved generation work buffers. Detection bounds and anchors use ClientRect/ClientPoint; guarded input converts configured Point values once and preserves client points. The retired ROI-to-baseline image normalization path and ROI caches are absent. See [native matching](NATIVE_MATCHING.md). Inventory vision then compares only this run's enabled available/purchased target states. Slots already completed in the current inventory are excluded before template matching; each remaining slot keeps only the highest-confidence classification. Repeated purchased-state skips are emitted once as a per-scan summary rather than synchronously once per frame. A scan result becomes actionable only after the same ordered `(slot_id, target_id)` list appears for the configured consecutive-frame count; the empty result uses the same rule. Mandatory targets are always enabled, and Friendship Points are matched only for runs whose checkbox was selected.

Shop scroll candidates, pairwise stability, and changed-pixel measurements still use the configured product-content ROI. Final content verification uses the complete top-slot item rectangles projected once through the full-client transform. A candidate requires at least `scroll.minimum_content_rows` (default two) distinct, vertically non-overlapping icon rows scoring at least 0.75; incomplete destination search windows and low-texture rows cannot supply evidence. All proposed translations and zero motion are scored before selection. Candidate quality is the weakest of its strongest N qualifying rows, where N is the required row count. The best quality must lead the strongest distinct translation by `scroll.minimum_content_margin` (default 0.05); candidates within the existing twice-alignment tolerance represent the same translation. Close distinct candidates remain ambiguous. Zero, downward, and insufficient upward motion are not filtered to force a valid scroll; the selected result must still pass the existing displacement/change gates. Product names contribute to proposals, while fixed background and short/dim name quadrants no longer veto icon evidence. Failed primary candidates retain their per-row scores when none qualifies. `ScrollContentCandidate` retains all scores, qualifying-row counts, quality, and baseline-unit translations; `scroll_settle_trace.content_candidate_checks` aggregates each content check with its sample number, selection, runner-up, margin, and reason.

Inventory readiness is separate from stable target identity. A feature-local SceneWindow requires a clear shop scene, populated vertical bands in the configured icon rows, at least three fresh frames across 200 ms, and both neighbor and first-to-last stability. Raw BGR changes are measured in icon interiors (baseline inset 32 x 24), with a per-row 20% limit as requested on 2026-10-21; the whole-row layout and final motion evidence remain unchanged. Existing shop target templates detect the stationary reward toast only in its dedicated upper ROI. The scanner hands its last validated FrameSample directly to scroll_to_bottom; refresh result reuse requires that same readiness evidence. Input/recovery invalidation, a scoped 500 ms evidence age check, and cleanup prevent stale handoffs. Network recovery after scrolling invalidates the original reference and stops rather than rebasing it. Pre-scroll waiting retains the scan deadline; post-scroll checks use the configured 1000 ms deadline, including scene detection and pixel work; successful confirmation still exits immediately.

When refresh balance confirmation finishes before top readiness/identity confirmation, InventoryScanner retains the existing SceneWindow, identity key, and consecutive count as a single-use continuation. Top scanning resumes it only with no completed slots, the same input epoch/recovery generation, and a current last frame; otherwise it starts fresh. Refresh accounting still completes at the original confirmed-balance boundary. Successful scrolling registers its exact returned sample tuple for one bottom handoff. Each FrameSample may carry immutable SceneSampleEvidence binding the frame identity, capture time, generation, viewport, and previously measured neighbor/span results. Bottom scanning replays the same SceneWindow rules and still recognizes target identity on each frame; it skips only measurements already bound to those exact frames/times. Missing comparison evidence falls back to the normal measurement. Recovery/input invalidation or expired handoff evidence discards the remaining samples and resumes fresh capture. No evidence survives purchase, refresh, reentry, or cleanup.

Shop inventory scanning, post-refresh balance confirmation, and scroll settling schedule the next capture from the previous capture time plus the configured polling interval; recognition time consumes that interval. Only the positive remainder is slept, bounded by the existing stage deadline. They still require distinct frame times and the original minimum stable span. Inventory and refresh performance logs expose `top_continuation_frames`, `scene_frames_reused`/`scene_comparisons_reused` where applicable, `sample_wait_ms`, and `poll_schedule=capture_interval`; these distinguish transferred evidence from fresh work. Other runtime polling loops retain their existing scheduling.

Readiness measurements explicitly select the current viewport. The configuration layout helper validates uniform calibrated row geometry/pitch and derives complete visible rows from each viewport anchor: top rows 1–4 and bottom rows 3–6. Partial rows are excluded before applying the pixel inset. This is independent of which new slots still need purchase scanning. Per-row client bounds and changes are retained in observations; scroll traces separate neighbor and first-to-last row vectors, while scan/refresh diagnostics retain the last comparison.

Adaptive scroll logging is aggregate rather than per-sample event spam. `scroll_settle_trace` records sample times, pairwise shifts/responses/change fractions, consecutive-stability counts, actual settle duration, and early-exit saving. `scroll_verified` retains the full-resolution before-to-final displacement evidence and adds the settle summary. `performance_stage stage=scroll_to_bottom` reports both the configured maximum and actual settle time. The following bottom `inventory_scan` stage reports `cache_outcome`, cached-frame availability/usage, cached stable suffix, fresh-frame count, and residual cache wait so live evidence can distinguish a direct hit from bounded fallback.

Scroll timing uses the runtime active clock for reference age, sampling intervals and the original total deadline. The normal scroll_settle_trace retains recognition evidence and total active elapsed time on success or timeout; trace output failure cannot replace the business result. Interruptions propagate through the existing run-stop and performance-stage reporting. Per-stage/candidate timers, profiling counters and the offline timing-summary command have been removed; no screenshot or calculation is added for profiling.


WGC borrows the validated BGRA bitmap buffer, clips ContentSize, resolves the existing client rectangle and copies that rectangle once into owned contiguous memory before releasing the buffer. The full-bitmap helper remains available to tools. Source/receive timestamp gates, post-copy freshness, window checks and resource cleanup stay in place; capture_copy_ms now includes client cropping. ShopVision retains only top/bottom layout and one client geometry's projected row rectangles, invalidated by configuration geometry or frame geometry changes. Reward-toast matching shares a call-local contiguous ROI with the original full-client transform/global origin and all configured targets; matching algorithms and row texture checks remain unchanged.

For a three-frame window with two required stable observations, the first two samples retain their existing cadence. After two ready, stable samples, the next capture starts no earlier than max(first completion + minimum stable span, second capture start + poll interval), bounded by the original deadline. All final checks still use actual completion timestamps and raw pixels. Failures reset the window as before; other frame-count configurations retain capture_interval scheduling. No public capture/vision signature or recognition threshold changes.

Within either viewport, the first enabled match in calibrated slot order is purchased. A successful purchase increments its target count exactly as before; the original purchase_outcome/three-frame sold-out-button rules are unchanged. A local post-purchase scope may be derived only from correctly ordered calibrated matches and an unchanged inventory context, allowing exactly purchase()'s own one invalidation. It retains slot ids and immutable context (epoch, recovery generation, window/transform/bounds, layout, enabled targets), not frame or click evidence. Targets must be freshly rediscovered before subsequent clicks. Top scopes require a previously observed later target; the last top target instead uses full scanning. Empty local top results also return to full scanning, so only a complete clean top window can produce the scroll reference. Bottom scopes cover the remaining configured slots; an exhausted bottom viewport performs the existing stable shop-control confirmation without rescanning goods, then hands control back to the unmodified strategy/budget/refresh/exit flow.

Readiness methods accept optional keyword-only slot_ids; None preserves whole-viewport readiness and the toast guard. Local scopes must be known nonempty suffixes of complete configured rows, exclude the first top row and remain below the configured reward-detection region. The phase ROI is the bounding union of the retained full item rectangles; texture, native row-pixel, neighbor/span, frame-count, duration and matching thresholds remain unchanged. The reward-detection ROI is not used as an occlusion mask. Local windows neither consume whole-viewport certificates nor produce scroll references. Geometry caches include row-set identity, preventing full/local projection aliasing. Recovery or any extra context invalidation discards local evidence and restores the full scan within its existing deadline; controls-only completion falls back to full scanning when revoked. Post-purchase scopes live only inside scan_viewport. Completed-slot suppression and mandatory-target accounting still apply across the inventory.

post_purchase_scan_selected records full_viewport, post_purchase_slots or controls_only and its scope; post_purchase_scope_fallback records invalidation reasons. inventory_scan performance records include requested/effective slot ids and scope, while post_purchase_finish times controls-only completion and bottom_purchase_fast_finish records actual completion. These records carry scoped_followup_v1 where applicable. No extra timing instrumentation or game action is triggered by logging.

Refresh is allowed only when `spent + fixed_cost <= limit`. First entry, re-entry, strategy/network recovery, ambiguity, and retry/failure boundaries require a fresh stable Sky Stone parse. The strict gem/plus template is searched only within the full-width top-bar band, never the whole client. From the detected icon-relative start, vision examines a bounded corridor to the client edge and decodes only the first baseline-aligned contiguous digit run; a template-derived large gap ends the run before distant notification controls, while an unsafe component inside the run rejects the entire balance. Digit count is not preset. Both bands stay in baseline coordinates and use the same ROI projection/cache path as other recognition on non-reference clients. After an exact confirmed refresh, the resulting balance is trusted only while the engine remains in the same certain in-shop state; an ordinary next refresh reuses that value instead of immediately rereading the same three frames. The engine clicks refresh once, requires both the refresh-specific prompt and blue confirmation button to appear stably, then clicks refresh confirmation once. Internal spending increases only when the post-confirm balance is stably equal to `before - 3`. The unchanged old value remains pending until timeout; any other stable delta terminates with `refresh_balance_mismatch` and no internal count. Frames that already show the exact expected balance also accumulate the next top inventory's stability, but no inventory action is allowed until exact balance confirmation succeeds. A concurrent stable top result is consumed once by the next cycle; otherwise the original independent stable top scan runs unchanged. If the refresh exactly consumes the budget, the new top and bottom viewports are fully scanned before the next refresh check terminates the run.

The 180-second strategy recovery can leave the main screen in its UI-hidden standby state. After that wait only, the engine sends one ordinary guarded click to the calibrated client center `(1161,653)` before the normal stable main-shop recognition and entry sequence. The 5-second recoveries skip the wake click.

Only a fully scanned inventory produced by a confirmed refresh advances the no-target strategy; the initial inventory does not. An available mandatory Covenant Bookmark or Mystic Medal resets the strategy immediately, while Friendship Points and purchased-state classifications do not. Consecutive no-target batches are `(13,13,13,10)`. Recovery after the first batch is stable exit, checkpointed 5-second main-screen wait, and stable re-entry; after the second it uses the same sequence with a 180-second wait; after the third it uses a 5-second wait. Completing the final batch raises `refresh_strategy_exhausted`. Manual stop and exact window/client geometry are rechecked throughout every wait. The currency budget is checked before recovery, so a run that cannot afford the next refresh stops without an unnecessary wait or exit.

Purchase result polling checks the insufficient-purchase-currency warning first. Otherwise a target-specific purchased icon in the original item ROI returns `SUCCESS`, retaining the immediate icon-success behavior. If that icon is missed, the shared `purchased_button` template is searched only around the unique slot whose item ROI equals the original match. The 331x98 template has 18 horizontal / 16 vertical pixels of baseline search padding, producing a 367x130 ROI centered at the slot's buy point. Unknown or ambiguous slot mappings and out-of-bounds search regions provide no fallback evidence.

A button match at the anchor threshold (0.93) returns `SUCCESS_BUTTON`; the engine counts it only after the configured consecutive-frame count (default 3). Every non-button result resets button stability. Insufficient-currency evidence has its own consecutive-frame counter; non-warning results reset it. Timeout remains ambiguous and terminates without retry or count changes. Neither the common button nor its outcome is used to infer target identity during inventory scanning. `purchase_result` logs the original slot and button stability; `purchase_counted.evidence` identifies the accepted success branch.

## Ports

`core/ports.py` defines replaceable runtime-integrity, window, capture, client-coordinate input, overlay, clock, and logger interfaces. Offline tests supply only fakes. The production WGC capture implementation is isolated in `platform/wgc_capture.py` and loaded only inside the automation worker; window-message input is isolated in `platform/input.py`; shared OpenCV algorithms live in `vision/`, with shop detection and scroll measurements in `features/shop/`; recognition accepts in-memory arrays only.

WGC validates positive `ContentSize` against the copied surface dimensions, then slices the valid top-left content before selecting a client-crop coordinate origin. Item/pool dimensions may include invisible borders while content matches DWM visible bounds; their inequality alone is not a capture failure. The existing crop maps valid content to DWM bounds or the outer window rectangle, or accepts client-sized content directly. Invalid/truncated content, unmappable geometry and client overflow still fail closed; engine window/display guards remain unchanged. Client output is contiguous, including the client-only path with surface padding.

Text logs include aggregate `performance_stage` records for inventory scans, scrolling, balance reads/waits, and refresh execution. Each record reports total duration plus capture and vision counts/times; no performance path persists screenshots or writes per-frame timing files.

## Penguin exchange

`features/penguin/flow.py` implements the separate 50-penguin batch flow. It composes `RuntimeContext` for guarded preparation/input and network recovery, selecting fresh capture after recovery and providing its own normal completion; it has no shop scan, refresh strategy or balance dependency. `AutomationSession.run_penguins` shares button stop control and resource cleanup. `RuntimeSnapshot` stores the feature id and separate successful-batch count/limit.

Penguin capture calls the shared network handler, discards the interrupted frame after recovery and captures anew. A recovery generation counter resets stable observations in penguin waits and shared entry confirmation/rechecks, including instant recovery with zero elapsed delay. Penguin recognition deadlines and maximum-price settling use the shared active clock, which excludes network recovery time; the UI elapsed timer continues. Recovery temporarily shows `重连中`, restores the previous activity status and resumes the pending recognition, never replaying a purchase submission. If recovery leaves an unknown purchase outcome, the existing result timeout still stops the run.

`features/penguin/configuration.py` validates the approved controls; `features/penguin/vision.py` recognizes them using shared matching and digit components. Every dialog needs max/cancel controls and the purchase-button body; the full 5100 template plus separately decoded yellow price digits authorize purchase. Only price-field pixels enter the digit reader. Existing digit shapes are reused with an independent yellow mask and confidence gate. Unknown prices are distinct from a positively decoded non-5100 price. A lower price after max must survive the settling window before normal completion.

`ui/pages/penguin.py` owns the purchase-limit input, read-only estimated leaf cost (`limit * 5100`, thousands-separated or `—` for invalid input), start button. The estimate reads no game balance. `StatsOverlay` switches between shop statistics and the three penguin lines while retaining drag/collapse. Reconfiguring hides stale labels before deferred Qt deletion. Returning to the function center after a stop hides the overlay; opening a settings page keeps it hidden until the next run positions it. Only one task can run at once.

Main-screen shop and sanctuary entry now share `RuntimeContext.enter_with_retry` and its destination/main recheck helper. A confirmed main-screen observation is passed directly to the click; timeout rechecks produce fresh observations before authorizing a retry. Both entries permit at most three clicks, preserve the shop's destination-first recheck behavior, and retain guarded input/manual stop. Only the main-screen sanctuary icon skips its former duplicate recognition. After sanctuary arrival, the forest entry retains its independent stable pre-click recognition. Purchase submissions do not use this helper.

The shared entry helper accepts an optional timeout override. Sanctuary passes 10,000 ms for both post-click arrival and subsequent destination/main recheck to tolerate its transition animation; shop retains its configured 5,000 ms. Successful stable detection continues immediately. Neither an unrecognized transition nor timeout alone permits another click; only stable main-screen evidence does. Manual stop interrupts both waits.

Function-center shop and penguin backgrounds both use `_ModuleCard._draw_cover_pixmap`: aspect-preserving cover scaling, centered cropping, `_IMAGE_ZOOM=1.0`, smooth interpolation and a scaled-size cache. The approved penguin v6 image is `assets/ui/penguin-card-background.png`; the shared card also supplies rounded clipping and the left-side title gradient. There is no penguin-specific scaling override.

## Template ownership and loading

Templates are grouped under `assets/templates/shop`, `penguin`, and `common/{digits,network}`. Shop startup loads 31 shop/shared images; penguin startup selects 13 shared images before adding its 13 validated controls. It does not read shop-only image files. The common YAML structure remains validated for both features. Historical manifests live in `docs/calibration`; the runtime penguin manifest stays beside its images. Future features follow [the template organization contract](../development/TEMPLATES.md).

### Shared main-screen column and recognition configuration

Both entry detectors read `rois.left_icon_column` and their respective `vision.entry_thresholds` (color and structure). Other penguin controls and price-digit thresholds also come from YAML. `resources/manifest.py` loads common/shop/penguin catalogs using the same baseline, contained-path, PNG-dimension and SHA-256 checks. Catalog `source.crop` describes provenance only and cannot generate or override search rectangles. The catalogs are runtime resources; source screenshots and historical calibration records are not runtime dependencies. Feature loading remains isolated (31 shop/shared images or 26 penguin/shared images).

### Shared startup navigation and bounded wake

Shop, penguin and Chaos initial entry use `RuntimeContext.wait_startup_icon`. The feature entry has priority on every frame. An absent entry enables shared menu detection; three stable menu observations start a bounded menu/tavern return, then the same feature entry confirms home. `vision/navigation.py` is composed into all three vision adapters and uses the existing matcher; its three explicit common resources and typed settings are loaded from `startup_navigation`. No feature imports another feature or changes the baseline matching algorithm.

Each return step waits three active seconds after input completion, with at most two clicks. Only the currently visible, stable source button and an absent destination authorize a retry; failure stops without wake fallback or restarting the return. The tavern is not searched at initial startup before a menu click. Runtime-local return state and attempt counts survive network recovery, while interrupted frames and stability are discarded. Stop/window/input guards and client anchors remain shared.

After an actual tavern click and stable home-entry confirmation, shared navigation buffers one active second before returning the same observation and client anchor. Normal buffering captures only for existing window, stop and network guards; it does not repeat entry detection. Direct startup and home arrival before a tavern click bypass the buffer. A network recovery invalidates the saved anchor and requires fresh stable entry evidence within the configured five-second entry budget, followed by a new full one-second buffer. Further recovery during reconfirmation clears stability without renewing that budget; interrupted frames are discarded and recovery time is excluded. There is no separate three-second readiness deadline or navigation/wake restart. Logs use home_buffer for waiting, completion and invalidation, and home_reconfirm for recovery confirmation or timeout.

When both entry and menu are absent, `wake_main_screen_startup` uses configured `main_screen_wake` and starts a fresh 10-second active recognition budget. Runtime-local `startup_wake_attempts` and `startup_last_wake_at` allow at most two attempts per run. A fresh frame missing both identities may authorize the second wake at least three active seconds after the first completed input. Current entry/menu evidence takes priority over that timer. Capture, matching or logging past the current deadline cannot authorize a wake. Normal reentry, destination checks, shop strategy wake and Chaos saved-exploration cleanup retain their existing behavior.

Network recovery excludes recovery duration from retry intervals and recognition budgets without resetting attempts or the last completion time. Public startup method signatures are unchanged. `RuntimeVisionPort` combines network and startup navigation detection; feature adapters delegate the latter to the common component. Startup input logs preserve `attempt`; confirmation preserves `wake_sent`/`wake_attempts` and adds `return_home`. Return-stage logs include retry and timeout reasons.

## v1.3.2 diagnostics and scroll fallback

Scroll verification retains the primary displacement gate and adds cumulative pairwise movement plus overlap checks when that gate fails. Stability, changed-pixel fraction and the existing timeout remain required; logs identify the accepted verification method.

Each run retains only its last successful game capture for stop diagnostics. Session cleanup and the worker exception path may write that cached frame once on an unexpected stop, without another capture. Expected stops (including manual stop and insufficient purchase funds) do not write an image. Snapshot failures cannot replace the original stop reason or bypass cleanup; the image shares its run's log retention group.

Chaos stage selection uses a shared guarded drag primitive. Each pointer step has its own
stop/input lock boundary, window check and active-deadline check. Short bursts throttle
network/page capture to 100 ms and recheck immediately after release; other callers retain
per-step capture by default. A held pointer is released before network recovery or on
stop/error; cleanup never appends further movement or navigation. Stage overscroll explicitly
allows signed client-relative coordinates beyond the visible client only after a valid
in-client press on the same window. Clicks, press locations and ordinary moves remain bounded.
The platform adapter sends these messages to that HWND without moving the real cursor.


Chaos text continuation has a narrow bright-background fallback in features/chaos/text_triangle.py. It compares inner/outer samples on all three sides of the existing template-sized downward triangle within its unchanged ROI; weak, wrong-shaped or spatially ambiguous results are rejected. Only event entry and text-to-map waits enable moving_text: a text_continue result can complete from one current frame. Event options, map views and all other detections retain the configured stable-frame rule. Existing guarded input, post-click settling, network recovery and manual stop remain in effect.


Camp and event rank rewards call the same ChaosFlow._rank_up capability. RoundState.next_rank_hero supplies their shared priority. The rank map is assigned old+1 after the expected numeric transition (camp/mushroom) or event_battle_rank_done (battle reward) is confirmed; continue_exploration is only a later click target, never part of rank success. Before leaving the result, chaos_rank_changed records hero, before/after and node kind. Network recovery never reruns the submission. EventContext injects this capability without importing ChaosFlow. Cairn keeps the last confirmed first-card snapshot and requires a different central card before its second close; the ROI and minimum image difference are configured in YAML.


HeroRecruitment records the pre-click hero Detection, then confirms selection
without rescanning the list identity. The target's left-detail name takes priority;
any other loaded detail-name match vetoes the frame, including ambiguous multiple
matches. If no detail name matches, a gold border near the original target must
contain its click anchor. The evidence Detection key distinguishes name from border,
so switching branches cannot accumulate three stable frames. Recruitment, rank-up
and departure jointly confirm that evidence and their submit button on each new
frame, then use the last stable button anchor without a second confirmation wait.
Departure retains its same-frame page-title guard. Existing network, stop, result
and once-only submission guards remain; conflicts only probe already-loaded names.

The red-phantom handler selects the configured departure hero only if still in the
roster. EventContext.depart delegates page-guarded identity selection, same-frame
selected identity/button confirmation and a single departure submission to ChaosFlow.
Only a stable Missing result permits the handler to remove the hero/rank, record the
departure once and cancel an obsolete Teacher replacement. It then uses the existing
two-card close helper for Missing and the distinct loot card, followed by text/map.
RoundState keeps departed heroes and the spent second-floor voucher per round. A
required restoration waits for the all-class ticket rather than silently forfeiting;
completion restores rank 1 and clears the departure marker. Event battles award no
experience and this event does not use the experience-finishing helper.


## Installed distribution (1.4.0)

Nuitka 4.1.3 compiles the standalone payload under build/nuitka; Inno Setup 6.7.3 packages it into the final Setup EXE. Application data remains in logs/state under the chosen installation root. Installed runs hold a global lifetime mutex; setup/uninstall refuse while it exists, and the application refuses launch during setup. The installer owns the machine-wide E7auto.Installed AppUserModelID and shortcuts. The compiled notifier validates that identity without writing per-user keys; source runs retain their independent E7auto.E7auto identity. No startup entry, persistent COM server, PATH or environment changes are added. Windows-owned notification history is unchanged.

An owned-file manifest permits scoped obsolete-file removal on upgrade. Logs/state survive upgrades and are removed on uninstall; unrelated user files are retained. Input snapshots and payload hashes are sealed against the final installer, then verified against an actually installed copy and an install/repair/uninstall acceptance record. See [installer workflow](../development/INSTALLER.md).

The clear pond has two first-choice identities in the global event index. Both dispatch to `clear_pond.run`; each Detection carries its own configured left/right anchor. The disabled-right variant gives its dim title zero shortlist weight, while retaining all masked text in full-template verification. Index validation requires at least one positive title weight and rejects empty positive-weight foregrounds. The original pond route, reward closing, temporal confirmation and same-floor return remain shared.
