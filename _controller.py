# Auto-generated state machine for EchoPress.
# Headless testable: no UI dependency.
import time
import threading
from _constants import (
    STATE_IDLE, STATE_PRESSING, STATE_RECORDING, STATE_CANCELLING,
    STATE_PLAYING, STATE_ERROR, LONG_PRESS_TRIGGER, CANCEL_SLIDE_PX,
    MAX_DURATION_SEC, MIN_DURATION_SEC, safe_remove, get_recording_path,
)


class EchoController:
    def __init__(self, recorder, player, audio_path_factory=None):
        self.recorder = recorder
        self.player = player
        self._audio_path_factory = audio_path_factory or get_recording_path
        self.state = STATE_IDLE
        self.recorded_path = None
        self._touch = None
        self._press_started_at = None
        self._record_started_at = None
        self._recording = False
        self._cancelling = False
        self._max_duration_hit = threading.Event()
        self._rec_count = 0
        self._play_count = 0
        self.on_state_change = None
        self.on_status = None
        self.on_cancel_hint = None
        self.on_error = None
        self.on_stats = None
        self.on_level = None

    def on_touch_down(self):
        if self.state == STATE_PLAYING:
            self.player.stop()
            safe_remove(self.recorded_path)
            self.recorded_path = None
            self._set_state(STATE_IDLE)
        if self.state in (STATE_IDLE, STATE_ERROR):
            self._touch = object()
            self._press_started_at = time.time()
            self._cancelling = False
            self._set_state(STATE_PRESSING)
            return True
        return False

    def on_touch_move(self, dy):
        if self._touch is None:
            return False
        if dy < -CANCEL_SLIDE_PX and self._recording:
            if not self._cancelling:
                self._cancelling = True
                if self.on_cancel_hint:
                    self.on_cancel_hint(True)
            return True
        return False

    def on_touch_up(self):
        if self._touch is None:
            return False
        self._touch = None
        if self.on_cancel_hint:
            self.on_cancel_hint(False)
        if self.state == STATE_PRESSING:
            self._set_state(STATE_IDLE)
            return True
        if self.state == STATE_RECORDING:
            if self._cancelling:
                self._cancel_recording()
            else:
                self._stop_recording_and_play()
            return True
        return False

    def tick(self):
        if self.state == STATE_PRESSING and self._press_started_at:
            if time.time() - self._press_started_at >= LONG_PRESS_TRIGGER:
                self._start_recording()
                return
        if self.state == STATE_RECORDING:
            try:
                level = self.recorder.get_current_level()
            except Exception:
                level = None
            if level is not None and self.on_level:
                self.on_level(level)
            elapsed = time.time() - (self._record_started_at or time.time())
            self._emit_status("\u25cf 录音中 " + f"{elapsed:.1f}s")
            if elapsed >= MAX_DURATION_SEC:
                if self._cancelling:
                    self._cancel_recording()
                else:
                    self._stop_recording_and_play()

    def _start_recording(self):
        path = self._audio_path_factory()
        try:
            self.recorder.start(path)
            self.recorded_path = path
            self._record_started_at = time.time()
            self._recording = True
            self._max_duration_hit.clear()
            self._set_state(STATE_RECORDING)
        except Exception as e:
            self._emit_error("录音启动失败: " + str(e))

    def _stop_recording_and_play(self):
        self._recording = False
        duration = time.time() - (self._record_started_at or time.time())
        path = self.recorded_path
        try:
            self.recorder.stop()
        except Exception as e:
            self._emit_error("录音停止失败: " + str(e))
            return
        if duration < MIN_DURATION_SEC:
            self._emit_status("太短了")
            safe_remove(path)
            self.recorded_path = None
            self._set_state(STATE_IDLE)
            return
        self._rec_count += 1
        self._emit_stats()
        self._start_playback(path)

    def _cancel_recording(self):
        self._recording = False
        try:
            self.recorder.stop()
        except Exception:
            pass
        safe_remove(self.recorded_path)
        self.recorded_path = None
        self._set_state(STATE_IDLE)

    def _start_playback(self, path):
        self._set_state(STATE_PLAYING)
        try:
            self.player.play(path, lambda: self._on_play_complete(path))
        except Exception as e:
            self._emit_error("回放失败: " + str(e))
            safe_remove(path)
            self.recorded_path = None
            self._set_state(STATE_IDLE)

    def _on_play_complete(self, path):
        safe_remove(path)
        if path == self.recorded_path:
            self.recorded_path = None
        self._play_count += 1
        self._emit_stats()
        self._set_state(STATE_IDLE)

    def _set_state(self, new_state):
        self.state = new_state
        if self.on_state_change:
            self.on_state_change(new_state)

    def _emit_status(self, msg):
        if self.on_status:
            self.on_status(msg)

    def _emit_error(self, msg):
            if self.on_error:
                self.on_error(msg)

    def _emit_stats(self):
        if self.on_stats:
            self.on_stats(self._rec_count, self._play_count)

    def shutdown(self):
        self._recording = False
        try:
            self.recorder.stop()
        except Exception:
            pass
        try:
            self.player.stop()
        except Exception:
            pass
        if self.recorded_path:
            safe_remove(self.recorded_path)
            self.recorded_path = None
