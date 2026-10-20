from types import SimpleNamespace

import pytest

from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.ports import WindowRef
from e7auto.core.types import Point, Size
from e7auto.core.types import ClientPoint
from e7auto.runtime.context import RuntimeContext
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController, StopExecution
from e7auto.vision.frames import CoordinateTransform
from tests.helpers import FakeClock, FakeInput, FakeLogger, make_config


def context(inputs=None):
    deps=SimpleNamespace(inputs=inputs or FakeInput(),clock=FakeClock(),logger=FakeLogger(),
                         vision=SimpleNamespace(network_connection_error=lambda _:None))
    ctx=RuntimeContext(make_config(),deps,StopController(),SnapshotPublisher(RuntimeSnapshot.chaos_run('drag',1),lambda _:None))
    ctx.window=WindowRef(123,'test','test.exe');ctx.transform=CoordinateTransform(Size(2322,1306),Size(1536,864))
    ctx.ensure_window=lambda **_:None;ctx.capture_raw=lambda:object()
    return ctx


def test_drag_transforms_every_point_and_releases_at_the_last_position():
    ctx=context();start,end=Point(1675,338),Point(1675,868)
    assert ctx.drag('test',start,end,duration_ms=480,step_ms=40)
    actions=ctx.deps.inputs.actions
    assert actions[0]==('pointer_down',ctx.transform.point(start),None)
    assert actions[-2]==('pointer_move',ctx.transform.point(end),None)
    assert actions[-1]==('pointer_up',ctx.transform.point(end),None)
    assert len([a for a in actions if a[0]=='pointer_move'])==12
    assert ctx.deps.clock.now==pytest.approx(.48)


@pytest.mark.parametrize('size',[Size(1536,864),Size(3072,1728)])
def test_native_detection_click_and_drag_are_not_scaled_twice(size):
    ctx=context();ctx.transform=CoordinateTransform(Size(2322,1306),size)
    start,end=ClientPoint(101,203),ClientPoint(101,403)
    ctx.click('native',start)
    assert ctx.deps.inputs.actions[-1]==('click',start,None)
    assert ctx.drag('native',start,end,duration_ms=100,step_ms=20)
    assert ctx.deps.inputs.actions[-1]==('pointer_up',end,None)


def test_stop_before_drag_produces_no_inputs():
    ctx=context();ctx.control.request(StopReason.MANUAL_STOP)
    with pytest.raises(StopExecution):ctx.drag('test',Point(100,100),Point(100,300),duration_ms=100,step_ms=20)
    assert ctx.deps.inputs.actions==[]


def test_move_failure_releases_and_preserves_input_failure():
    class Failing(FakeInput):
        def pointer_move(self,*_):raise RuntimeError('move failed')
    ctx=context(Failing())
    with pytest.raises(StopExecution) as error:ctx.drag('test',Point(100,100),Point(100,300),duration_ms=100,step_ms=20)
    assert error.value.reason==StopReason.INPUT_FAILURE
    assert [a[0] for a in ctx.deps.inputs.actions]==['pointer_down','pointer_up']


def test_page_guard_blocks_down_without_sending_cleanup_input():
    ctx=context()
    assert not ctx.drag('test',Point(100,100),Point(100,300),duration_ms=100,step_ms=20,frame_guard=lambda _:False)
    assert ctx.deps.inputs.actions==[]


def test_fast_burst_preserves_scaled_overscroll_and_defers_expensive_frame_checks():
    ctx=context();captures=[];moves=[];original=ctx.deps.inputs.pointer_move
    def capture():
        captures.append(ctx.deps.clock.now)
        ctx.deps.clock.sleep(.15)
        return object()
    def move(window,point,*,allow_outside_client=False):
        assert allow_outside_client
        moves.append((point,ctx.deps.clock.now))
        original(window,point,allow_outside_client=True)
    ctx.capture_raw=capture;ctx.deps.inputs.pointer_move=move
    start,end=Point(1675,338),Point(1675,5638)
    assert ctx.drag('fast',start,end,duration_ms=50,step_ms=10,
                    allow_outside_client=True,frame_check_ms=100)
    assert len(captures)==2
    assert [p for p,_ in moves]==[ctx.transform.point(Point(1675,y)) for y in (1398,2458,3518,4578,5638)]
    assert [t for _,t in moves]==pytest.approx([.16,.17,.18,.19,.20])
    assert ctx.deps.inputs.actions[-1]==('pointer_up',ctx.transform.point(end),None)


def test_slow_frame_checks_do_not_compress_following_movement_intervals():
    ctx=context();times=[]
    def capture():
        ctx.deps.clock.sleep(.025)
        return object()
    ctx.capture_raw=capture
    ctx.deps.inputs.pointer_move=lambda *_:times.append(ctx.deps.clock.now)
    assert ctx.drag('timing',Point(100,100),Point(100,300),duration_ms=50,step_ms=10)
    assert [b-a for a,b in zip(times,times[1:])]==pytest.approx([.035]*4)


@pytest.mark.parametrize('start,end,allow',[
    (Point(100,-1),Point(100,300),True),
    (Point(100,100),Point(100,5638),False),
    (Point(100,100),Point(100,60000),True),
])
def test_invalid_drag_coordinates_are_rejected_before_down(start,end,allow):
    ctx=context()
    with pytest.raises(StopExecution) as error:
        ctx.drag('invalid',start,end,duration_ms=50,step_ms=10,allow_outside_client=allow)
    assert error.value.reason==StopReason.INPUT_FAILURE and ctx.deps.inputs.actions==[]


def test_expired_step_guard_interrupts_fast_burst_and_releases():
    ctx=context()
    assert not ctx.drag('deadline',Point(100,100),Point(100,5638),duration_ms=50,step_ms=10,
                        allow_outside_client=True,frame_check_ms=100,
                        step_guard=lambda:ctx.deps.clock.now<.025)
    assert [a[0] for a in ctx.deps.inputs.actions]==['pointer_down','pointer_move','pointer_move','pointer_up']
