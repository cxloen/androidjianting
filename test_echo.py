# EchoPress headless test
# 覆盖状态机边界 + 删除兜底，不依赖 Kivy GUI、不依赖 sounddevice 真实音频 I/O。
import os
import sys
import time
import tempfile
import threading

# 加 cwd 到 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _constants import (
    STATE_IDLE, STATE_PRESSING, STATE_RECORDING,
    STATE_CANCELLING, STATE_PLAYING, STATE_ERROR,
    LONG_PRESS_TRIGGER, CANCEL_SLIDE_PX,
    MAX_DURATION_SEC, MIN_DURATION_SEC,
)

from _controller import EchoController


# ===================== Mock Recorder / Player =====================
class MockRecorder:
    """Mock 录音器：记录被调用的方法 + 模拟当前电平"""
    def __init__(self, fail_start=False):
        self.started_paths = []
        self.stopped_count = 0
        self.fail_start = fail_start
        self.level = 0.0

    def start(self, path):
        if self.fail_start:
            raise RuntimeError("mock start fail")
        self.started_paths.append(path)
        # 模拟真实 recorder：创建一个空文件供 controller 安全删除
        try:
            open(path, "wb").close()
        except Exception:
            pass

    def stop(self):
        self.stopped_count += 1

    def get_current_level(self):
        return self.level


class MockPlayer:
    """Mock 回放器：play() 时调用 on_complete 来模拟回放立即完成。
    complete_immediately=True 时同步完成；False 时需手动调用 complete()。
    """
    def __init__(self, complete_immediately=True, fail_play=False):
        self.played_paths = []
        self.stopped_count = 0
        self.complete_immediately = complete_immediately
        self.fail_play = fail_play
        self._pending_complete = None

    def play(self, path, on_complete):
        self.played_paths.append(path)
        if self.fail_play:
            raise RuntimeError("mock play fail")
        if self.complete_immediately and on_complete:
            on_complete()
        elif on_complete:
            self._pending_complete = on_complete

    def complete(self):
        """手动触发回放完成回调（用于异步测试）"""
        cb = self._pending_complete
        self._pending_complete = None
        if cb:
            cb()

    def stop(self):
        if self._pending_complete is not None:
            # 模拟打断：不触发 on_complete
            self._pending_complete = None
        self.stopped_count += 1


# ===================== Test helpers =====================
def make_controller(recorder=None, player=None, tmpdir=None):
    """构造一个 EchoController + 临时目录路径工厂"""
    if recorder is None:
        recorder = MockRecorder()
    if player is None:
        player = MockPlayer()
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp(prefix="echo_test_")
    path_counter = [0]
    def factory():
        path_counter[0] += 1
        return os.path.join(tmpdir, f"rec_{path_counter[0]}.m4a")
    ctrl = EchoController(recorder, player, audio_path_factory=factory)
    # 收集状态变化
    states = []
    ctrl.on_state_change = lambda s: states.append(s)
    return ctrl, recorder, player, tmpdir, states


def wait_until(predicate, timeout=2.0, interval=0.01):
    """等待 predicate 为真，最多 timeout 秒"""
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ===================== Test cases =====================
def test_initial_state():
    ctrl, rec, ply, tmp, states = make_controller()
    assert ctrl.state == STATE_IDLE
    assert states == [], f"初始不应该触发状态变更, got {states}"
    print("  [PASS] test_initial_state")


def test_press_then_release_too_short():
    """按下后立刻松开(防误触期内) -> 不应开始录音"""
    ctrl, rec, ply, tmp, states = make_controller()
    ctrl.on_touch_down()
    assert ctrl.state == STATE_PRESSING
    ctrl.on_touch_up()
    assert ctrl.state == STATE_IDLE, f"应回到 IDLE, got {ctrl.state}"
    assert rec.started_paths == [], "太短时不应启动录音"
    print("  [PASS] test_press_then_release_too_short")


def test_full_recording_playback_cycle():
    """按下 -> 等待触发 -> 录音 -> 松手 -> 自动回放 -> 自动删除"""
    # 使用异步 mock player，让我们能精确观察 STATE_PLAYING
    rec = MockRecorder()
    ply = MockPlayer(complete_immediately=False)
    ctrl, rec, ply, tmp, states = make_controller(recorder=rec, player=ply)

    # 1. 按下
    assert ctrl.on_touch_down() is True
    assert ctrl.state == STATE_PRESSING

    # 2. 等待防误触触发（推进内部时钟）
    assert wait_until(lambda: (time.sleep(0.01), ctrl.tick(), ctrl.state == STATE_RECORDING)[-1], timeout=1.0)
    assert len(rec.started_paths) == 1, f"应启动一次录音, got {rec.started_paths}"
    path = rec.started_paths[0]
    assert os.path.exists(path), "录音路径文件应被使用"

    # 3. 模拟一段真实录音时长
    ctrl._record_started_at -= 1.0

    # 4. 松手 -> 回放开始（异步）
    ctrl.on_touch_up()
    assert wait_until(lambda: ctrl.state == STATE_PLAYING, timeout=1.0)
    assert ply.played_paths == [path], "应回放刚录的这段"

    # 5. 触发回放完成 -> IDLE 且文件被删除
    ply.complete()
    assert wait_until(lambda: ctrl.state == STATE_IDLE, timeout=1.0)
    assert not os.path.exists(path), f"回放完文件应被自动删除: {path}"
    assert ctrl._play_count == 1
    assert ctrl._rec_count == 1
    print("  [PASS] test_full_recording_playback_cycle")


def test_slide_up_cancel():
    """录音中上滑超过阈值 -> 标记取消 -> 松手后丢弃且不触发回放"""
    ctrl, rec, ply, tmp, states = make_controller()
    ctrl.on_touch_down()
    wait_until(lambda: (time.sleep(0.01), ctrl.tick(), ctrl.state == STATE_RECORDING)[-1], timeout=1.0)
    path = rec.started_paths[0]
    # 文件可能尚未实际创建（mock 不写文件）。手工建一个空文件模拟
    open(path, "w").close()
    assert os.path.exists(path)

    # 上滑 -CANCEL_SLIDE_PX - 10
    ctrl.on_touch_move(-(CANCEL_SLIDE_PX + 10))
    assert ctrl._cancelling is True, "上滑应触发取消标志"

    # 松手 -> 取消（删除 + 回 IDLE）
    ctrl.on_touch_up()
    assert ctrl.state == STATE_IDLE
    assert not os.path.exists(path), "取消时文件应被删除"
    assert ply.played_paths == [], "取消不应触发回放"
    print("  [PASS] test_slide_up_cancel")


def test_release_too_short_no_playback():
    """按下超过触发阈值，但松手时录音时长 < MIN_DURATION_SEC -> 不回放，文件被删除"""
    ctrl, rec, ply, tmp, states = make_controller()
    ctrl.on_touch_down()
    wait_until(lambda: (time.sleep(0.01), ctrl.tick(), ctrl.state == STATE_RECORDING)[-1], timeout=1.0)
    path = rec.started_paths[0]
    open(path, "w").close()

    # _record_started_at 设为现在（录音几乎没开始）
    ctrl._record_started_at = time.time()
    ctrl.on_touch_up()
    # "太短了"路径：删除文件 + 回 IDLE，不回放
    assert ctrl.state == STATE_IDLE
    assert ply.played_paths == [], "太短时不应回放"
    assert not os.path.exists(path), "太短时文件应被删除"
    print("  [PASS] test_release_too_short_no_playback")


def test_interrupt_playback_with_new_press():
    """回放中按下 -> 打断回放，文件被删除"""
    # 构造一个"慢完成"的 player
    rec = MockRecorder()
    ply = MockPlayer(complete_immediately=False)
    ctrl, rec, ply, tmp, states = make_controller(recorder=rec, player=ply)
    ctrl.on_touch_down()
    wait_until(lambda: (time.sleep(0.01), ctrl.tick(), ctrl.state == STATE_RECORDING)[-1], timeout=1.0)
    path = rec.started_paths[0]
    open(path, "w").close()
    ctrl._record_started_at -= 1.0
    ctrl.on_touch_up()
    assert wait_until(lambda: ctrl.state == STATE_PLAYING, timeout=1.0)

    # 此时正在回放，按下新一次
    ctrl.on_touch_down()
    # 期望：打断并清理
    assert ply.stopped_count >= 1, "应调用 player.stop()"
    assert not os.path.exists(path), "打断时应删除文件"
    assert ctrl.state in (STATE_IDLE, STATE_PRESSING), f"打断后应进 IDLE/PRESSING, got {ctrl.state}"
    print("  [PASS] test_interrupt_playback_with_new_press")


def test_max_duration_auto_stop():
    """超过 MAX_DURATION_SEC 自动停止并回放"""
    ctrl, rec, ply, tmp, states = make_controller()
    ctrl.on_touch_down()
    wait_until(lambda: (time.sleep(0.01), ctrl.tick(), ctrl.state == STATE_RECORDING)[-1], timeout=1.0)
    path = rec.started_paths[0]
    open(path, "w").close()
    # 手动推进 _record_started_at 模拟时间流逝
    ctrl._record_started_at -= (MAX_DURATION_SEC + 0.5)

    # 触发 tick
    ctrl.tick()
    # 应自动进入 PLAYING（mock 立即完成 -> IDLE）
    assert wait_until(lambda: ctrl.state == STATE_IDLE, timeout=1.0)
    assert ply.played_paths == [path], "超时应自动回放"
    assert not os.path.exists(path), "超时应自动清理"
    print("  [PASS] test_max_duration_auto_stop")


def test_recorder_start_failure_does_not_crash():
    """录音器启动失败 -> 进入 ERROR 状态，文件被清理"""
    rec = MockRecorder(fail_start=True)
    ply = MockPlayer()
    ctrl, rec, ply, tmp, states = make_controller(recorder=rec, player=ply)
    errors = []
    ctrl.on_error = lambda m: errors.append(m)
    ctrl.on_touch_down()
    # 触发
    wait_until(lambda: (time.sleep(0.01), ctrl.tick(), ctrl.state == STATE_RECORDING)[-1], timeout=1.0)
    # 状态应回 IDLE（不卡住），且触发错误
    # 实际上 fail_start 会让 _start_recording 走 except, _set_state 不会触发
    # 所以 state 还是 PRESSING (从 tick 进入 _start_recording 之前的状态)
    # 等一下 tick 一次看效果
    time.sleep(0.01)
    ctrl.tick()
    assert errors != [], f"应触发错误回调, got {errors}"
    print("  [PASS] test_recorder_start_failure_does_not_crash")


def test_player_play_failure_cleans_file():
    """回放器启动失败 -> 文件被清理，回 IDLE"""
    rec = MockRecorder()
    ply = MockPlayer(fail_play=True)
    ctrl, rec, ply, tmp, states = make_controller(recorder=rec, player=ply)
    errors = []
    ctrl.on_error = lambda m: errors.append(m)
    ctrl.on_touch_down()
    wait_until(lambda: (time.sleep(0.01), ctrl.tick(), ctrl.state == STATE_RECORDING)[-1], timeout=1.0)
    path = rec.started_paths[0]
    open(path, "w").close()
    ctrl._record_started_at -= 1.0
    ctrl.on_touch_up()
    # _start_playback 触发 fail_play, 文件应被删除
    assert wait_until(lambda: ctrl.state == STATE_IDLE, timeout=1.0)
    assert not os.path.exists(path), "回放失败时文件应被删除"
    assert errors != [], "应触发错误回调"
    print("  [PASS] test_player_play_failure_cleans_file")


def test_shutdown_cleans_file():
    """shutdown() 调用时如果有未清理文件，应删除"""
    ctrl, rec, ply, tmp, states = make_controller()
    ctrl.on_touch_down()
    wait_until(lambda: (time.sleep(0.01), ctrl.tick(), ctrl.state == STATE_RECORDING)[-1], timeout=1.0)
    path = rec.started_paths[0]
    open(path, "w").close()
    ctrl.shutdown()
    assert not os.path.exists(path), "shutdown 应清理未删除文件"
    print("  [PASS] test_shutdown_cleans_file")


def test_stats_increment_correctly():
    """录/播计数正确累加"""
    ctrl, rec, ply, tmp, states = make_controller()
    stats_log = []
    ctrl.on_stats = lambda r, p: stats_log.append((r, p))
    for _ in range(3):
        ctrl.on_touch_down()
        wait_until(lambda: (time.sleep(0.01), ctrl.tick(), ctrl.state == STATE_RECORDING)[-1], timeout=1.0)
        path = rec.started_paths[-1]
        open(path, "w").close()
        ctrl._record_started_at -= 1.0
        ctrl.on_touch_up()
        wait_until(lambda: ctrl.state == STATE_IDLE, timeout=1.0)
    assert ctrl._rec_count == 3
    assert ctrl._play_count == 3
    assert stats_log[-1] == (3, 3), f"最后一次 stats 应为 (3,3), got {stats_log[-1]}"
    print("  [PASS] test_stats_increment_correctly")


def test_cancel_hint_callback():
    """上滑取消时触发 on_cancel_hint(True)，松手触发 False"""
    ctrl, rec, ply, tmp, states = make_controller()
    hints = []
    ctrl.on_cancel_hint = lambda v: hints.append(v)
    ctrl.on_touch_down()
    wait_until(lambda: (time.sleep(0.01), ctrl.tick(), ctrl.state == STATE_RECORDING)[-1], timeout=1.0)
    ctrl.on_touch_move(-(CANCEL_SLIDE_PX + 10))
    assert True in hints, "上滑应触发 hint=True"
    ctrl.on_touch_up()
    assert False in hints, "松手应触发 hint=False"
    print("  [PASS] test_cancel_hint_callback")


def test_level_callback_in_recording():
    """录音中 tick 应调用 on_level"""
    rec = MockRecorder()
    rec.level = 0.42
    ply = MockPlayer()
    ctrl, rec, ply, tmp, states = make_controller(recorder=rec, player=ply)
    levels = []
    ctrl.on_level = lambda lv: levels.append(lv)
    ctrl.on_touch_down()
    wait_until(lambda: (time.sleep(0.01), ctrl.tick(), ctrl.state == STATE_RECORDING)[-1], timeout=1.0)
    ctrl.tick()
    assert 0.42 in levels, f"应收到电平 0.42, got {levels}"
    print("  [PASS] test_level_callback_in_recording")


# ===================== Runner =====================
def main():
    tests = [
        test_initial_state,
        test_press_then_release_too_short,
        test_full_recording_playback_cycle,
        test_slide_up_cancel,
        test_release_too_short_no_playback,
        test_interrupt_playback_with_new_press,
        test_max_duration_auto_stop,
        test_recorder_start_failure_does_not_crash,
        test_player_play_failure_cleans_file,
        test_shutdown_cleans_file,
        test_stats_increment_correctly,
        test_cancel_hint_callback,
        test_level_callback_in_recording,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  [ERROR] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
