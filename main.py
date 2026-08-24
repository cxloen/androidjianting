"""
EchoPress - 按住录音，松手回放，回放完自动删除
不留下任何录音文件，按下开口，松手即听，听完全自动消失。

桌面调试：Windows/Mac/Linux 用 sounddevice/scipy 模拟
安卓真机：用 Android MediaRecorder/MediaPlayer 原生接口
"""

import os
import time
import threading
from _constants import *

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.floatlayout import FloatLayout
from kivy.graphics import Color, Rectangle, Line, Ellipse
from kivy.clock import Clock
from kivy.utils import platform as kivy_platform

IS_ANDROID = kivy_platform == 'android'

# 平台特有导入
if IS_ANDROID:
    try:
        from android.permissions import request_permissions, Permission, check_permission
        from jnius import autoclass
        MediaRecorder = autoclass('android.media.MediaRecorder')
        MediaPlayer = autoclass('android.media.MediaPlayer')
        AudioSource = autoclass('android.media.MediaRecorder$AudioSource')
        OutputFormat = autoclass('android.media.MediaRecorder$OutputFormat')
        AudioEncoder = autoclass('android.media.MediaRecorder$AudioEncoder')
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
    except Exception as e:
        print(f'[WARN] Android类加载失败: {e}')
        IS_ANDROID = False
else:
    try:
        import sounddevice as sd
        import numpy as np
        from scipy.io import wavfile
    except ImportError:
        sd = None
        np = None
        wavfile = None
        print('[WARN] sounddevice/numpy 未安装，桌面调试不可用')
        print('       请运行: pip install sounddevice numpy scipy')

# 录音参数





# ===================== 录音器抽象 =====================
class RecorderBase:
    def start(self, path):
        raise NotImplementedError
    def stop(self):
        raise NotImplementedError
    def get_current_level(self):
        return None


class AndroidRecorder(RecorderBase):
    def __init__(self):
        self.rec = None
        self.path = None

    def start(self, path):
        self.path = path
        rec = MediaRecorder()
        rec.setAudioSource(AudioSource.MIC)
        rec.setOutputFormat(OutputFormat.MPEG_4)
        rec.setAudioEncoder(AudioEncoder.AAC)
        rec.setAudioEncodingBitRate(64000)
        rec.setAudioSamplingRate(SAMPLE_RATE)
        rec.setOutputFile(path)
        rec.setMaxDuration(MAX_DURATION_SEC * 1000)
        rec.prepare()
        rec.start()
        self.rec = rec
        return True

    def get_current_level(self):
        if self.rec is None:
            return 0.0
        try:
            amp = self.rec.getMaxAmplitude()
            return min(1.0, amp / 8000.0)
        except Exception:
            return 0.0

    def stop(self):
        if self.rec is not None:
            try:
                self.rec.stop()
            except Exception:
                pass
            try:
                self.rec.release()
            except Exception:
                pass
            self.rec = None


class DesktopRecorder(RecorderBase):
    def __init__(self):
        self._stream = None
        self._chunks = []
        self._lock = threading.Lock()
        self._latest_level = 0.0
        self._path = None

    def _callback(self, indata, frames, time_info, status):
        with self._lock:
            self._chunks.append(indata.copy())
            rms = float(np.sqrt(np.mean(indata ** 2)))
            self._latest_level = min(1.0, rms / 0.3)

    def start(self, path):
        self._path = path
        self._chunks = []
        self._latest_level = 0.0
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=self._callback,
        )
        self._stream.start()
        return True

    def get_current_level(self):
        return self._latest_level

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        try:
            with self._lock:
                if self._chunks:
                    data = np.concatenate(self._chunks, axis=0)
                    pcm = (data[:, 0] * 32767).astype(np.int16)
                    wavfile.write(self._path, SAMPLE_RATE, pcm)
                    return True
        except Exception as e:
            print(f'[WARN] wav 写入失败: {e}')
        return False


# ===================== 回放器抽象 =====================
class PlayerBase:
    def play(self, path, on_complete):
        raise NotImplementedError
    def stop(self):
        raise NotImplementedError


class AndroidPlayer(PlayerBase):
    def __init__(self):
        self.player = None
        self._on_complete = None

    def play(self, path, on_complete):
        self._on_complete = on_complete
        p = MediaPlayer()
        p.setDataSource(path)
        p.prepare()
        # pyjnius 回调要切回 Kivy 主线程
        def _on_done(mp):
            Clock.schedule_once(lambda dt: self._handle_complete())
        p.setOnCompletionListener(MediaPlayer.OnCompletionListener(_on_done))
        p.start()
        self.player = p

    def _handle_complete(self):
        self._safe_release()
        cb = self._on_complete
        self._on_complete = None
        if cb:
            cb()

    def stop(self):
        if self.player is not None:
            try:
                if self.player.isPlaying():
                    self.player.stop()
            except Exception:
                pass
        self._safe_release()

    def _safe_release(self):
        if self.player is not None:
            try:
                self.player.release()
            except Exception:
                pass
            self.player = None


class DesktopPlayer(PlayerBase):
    def __init__(self):
        self._stop_flag = threading.Event()

    def play(self, path, on_complete):
        self._stop_flag.clear()
        rate, data = wavfile.read(path)
        # 转 float 防止 sounddevice 报错
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        def _worker():
            try:
                sd.play(data, samplerate=rate)
                sd.wait()
            except Exception as e:
                print(f'[WARN] 桌面回放失败: {e}')
            if not self._stop_flag.is_set():
                Clock.schedule_once(lambda dt: on_complete())
        threading.Thread(target=_worker, daemon=True).start()

    def stop(self):
        self._stop_flag.set()
        try:
            sd.stop()
        except Exception:
            pass


# ===================== 圆形可视化 =====================
class LevelCircle(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.level = 0.0
        self.bind(size=self._update, pos=self._update)
        Clock.schedule_interval(self._animate, 1.0 / 30.0)

    def set_level(self, level):
        self.level = min(1.0, max(0.0, level))

    def _animate(self, dt):
        self._update()

    def _update(self, *args):
        self.canvas.clear()
        with self.canvas:
            cx = self.x + self.width / 2
            cy = self.y + self.height / 2
            r = min(self.width, self.height) / 2 - 6
            Color(0.15, 0.15, 0.2, 1)
            Ellipse(pos=(cx - r, cy - r), size=(2 * r, 2 * r))
            inner = r * (0.3 + 0.6 * self.level)
            red = 0.16 + 0.7 * self.level
            green = 0.30 + 0.3 * (1.0 - self.level)
            blue = 0.48 + 0.4 * (1.0 - self.level)
            Color(red, green, blue, 1)
            Ellipse(pos=(cx - inner, cy - inner), size=(2 * inner, 2 * inner))


# ===================== 主应用 =====================
class EchoPressApp(App):
    def build(self):
        self.title = 'EchoPress'
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

        # 平台组件
        if IS_ANDROID:
            self.recorder = AndroidRecorder()
            self.player = AndroidPlayer()
        else:
            self.recorder = DesktopRecorder()
            self.player = DesktopPlayer()

        # 启动时清理残留
        self._cleanup_stale_files()
        # 请求权限
        if IS_ANDROID:
            try:
                request_permissions([Permission.RECORD_AUDIO])
            except Exception as e:
                print(f'[WARN] 权限申请失败: {e}')

        # UI 布局
        root = FloatLayout()

        # 顶部标题
        self.title_label = Label(
            text='[size=20][b]EchoPress[/b][/size]\n[size=12]按住录音，松手回放[/size]',
            markup=True,
            size_hint=(1, None),
            height=70,
            pos_hint={'top': 1},
            color=(0.91, 0.92, 0.94, 1),
        )
        root.add_widget(self.title_label)

        # 取消提示
        self.cancel_hint = Label(
            text='[size=16]↑ 上滑取消[/size]',
            markup=True,
            size_hint=(1, None),
            height=40,
            pos_hint={'top': 0.88},
            color=(1, 0.4, 0.4, 0),
        )
        root.add_widget(self.cancel_hint)

        # 电平可视化
        self.level_circle = LevelCircle(size_hint=(None, None), size=(180, 180))
        self.level_circle.pos_hint = {'center_x': 0.5, 'center_y': 0.62}
        root.add_widget(self.level_circle)

        # 大圆按钮（透明覆盖层，捕获触摸）
        self.big_btn = Widget(size_hint=(None, None), size=(220, 220))
        self.big_btn.pos_hint = {'center_x': 0.5, 'center_y': 0.62}
        self.big_btn.bind(on_touch_down=self._on_touch_down,
                          on_touch_move=self._on_touch_move,
                          on_touch_up=self._on_touch_up)
        root.add_widget(self.big_btn)

        # 状态文字
        self.status_label = Label(
            text='[size=18]准备就绪[/size]',
            markup=True,
            size_hint=(1, None),
            height=40,
            pos_hint={'center_y': 0.30},
            color=(0.61, 0.63, 0.67, 1),
        )
        root.add_widget(self.status_label)

        # 统计
        self.stats_label = Label(
            text='录制:0  播放:0',
            size_hint=(1, None),
            height=30,
            pos_hint={'center_y': 0.08},
            color=(0.4, 0.4, 0.45, 1),
        )
        root.add_widget(self.stats_label)

        # 状态轮询（电平更新、计时、时长上限）
        Clock.schedule_interval(self._tick, 1.0 / 20.0)

        return root

    # ===================== 触摸事件 =====================
    def _on_touch_down(self, widget, touch):
        if self.state == STATE_PLAYING:
            # 回放中按下：打断并开始新一轮
            self.player.stop()
            safe_remove(self.recorded_path)
            self.recorded_path = None
            self._enter_state(STATE_IDLE)
        if self.state in (STATE_IDLE, STATE_ERROR):
            if self.big_btn.collide_point(*touch.pos):
                self._touch = touch
                self._press_started_at = time.time()
                self._cancelling = False
                self._enter_state(STATE_PRESSING)
                touch.grab(self.big_btn)
                return True
        return False

    def _on_touch_move(self, widget, touch):
        if touch is self._touch:
            # 向上滑动 CANCEL_SLIDE_PX 以上视为取消
            if touch.dy < -CANCEL_SLIDE_PX and self._recording:
                if not self._cancelling:
                    self._cancelling = True
                    self._set_cancel_hint_visible(True)
            return True
        return False

    def _on_touch_up(self, widget, touch):
        if touch is self._touch:
            self._touch = None
            self._set_cancel_hint_visible(False)
            if self.state == STATE_PRESSING:
                # 还没真正开始录音就松手
                self._enter_state(STATE_IDLE)
                return True
            if self.state == STATE_RECORDING:
                if self._cancelling:
                    self._cancel_recording()
                else:
                    self._stop_recording_and_play()
                return True
        return False

    # ===================== 录音控制 =====================
    def _start_recording(self):
        path = get_recording_path()
        try:
            self.recorder.start(path)
            self.recorded_path = path
            self._record_started_at = time.time()
            self._recording = True
            self._max_duration_hit.clear()
            self._enter_state(STATE_RECORDING)
        except Exception as e:
            self._show_error(f'录音启动失败: {e}')

    def _stop_recording_and_play(self):
        self._recording = False
        duration = time.time() - (self._record_started_at or time.time())
        path = self.recorded_path
        try:
            self.recorder.stop()
        except Exception as e:
            self._show_error(f'录音停止失败: {e}')
            return
        # 太短
        if duration < MIN_DURATION_SEC:
            self._show_toast('太短了')
            safe_remove(path)
            self.recorded_path = None
            self._enter_state(STATE_IDLE)
            return
        # 正常回放
        self._rec_count += 1
        self._start_playback(path)

    def _cancel_recording(self):
        self._recording = False
        try:
            self.recorder.stop()
        except Exception:
            pass
        path = self.recorded_path
        safe_remove(path)
        self.recorded_path = None
        self._enter_state(STATE_IDLE)

    # ===================== 回放 =====================
    def _start_playback(self, path):
        self._enter_state(STATE_PLAYING)
        try:
            self.player.play(path, lambda: self._on_play_complete(path))
        except Exception as e:
            self._show_error(f'回放失败: {e}')
            safe_remove(path)
            self.recorded_path = None
            self._enter_state(STATE_IDLE)

    def _on_play_complete(self, path):
        # 核心：放完自动删除
        safe_remove(path)
        if path == self.recorded_path:
            self.recorded_path = None
        self._play_count += 1
        self._enter_state(STATE_IDLE)

    # ===================== 状态/UI =====================
    def _enter_state(self, new_state):
        self.state = new_state
        if new_state == STATE_IDLE:
            self._set_status('准备就绪')
            self._set_button_color(STATE_IDLE)
            self.level_circle.set_level(0.0)
        elif new_state == STATE_PRESSING:
            self._set_status('按下中...')
            self._set_button_color(STATE_PRESSING)
        elif new_state == STATE_RECORDING:
            self._set_status('● 录音中')
            self._set_button_color(STATE_RECORDING)
        elif new_state == STATE_PLAYING:
            self._set_status('▶ 回放中')
            self._set_button_color(STATE_PLAYING)
        elif new_state == STATE_CANCELLING:
            self._set_status('松开取消')
        elif new_state == STATE_ERROR:
            self._set_button_color(STATE_ERROR)
        self._update_stats()

    def _set_status(self, msg):
        self.status_label.text = f'[size=18]{msg}[/size]'

    def _set_cancel_hint_visible(self, visible):
        if visible:
            self.cancel_hint.color = (1, 0.4, 0.4, 1)
        else:
            self.cancel_hint.color = (1, 0.4, 0.4, 0)

    def _set_button_color(self, state):
        # 通过电平环颜色变化表达按钮状态
        if state == STATE_IDLE:
            self.level_circle.set_level(0.05)
        elif state == STATE_PRESSING:
            self.level_circle.set_level(0.2)
        elif state == STATE_RECORDING:
            pass  # 由电平驱动
        elif state == STATE_PLAYING:
            self.level_circle.set_level(0.7)
        elif state == STATE_ERROR:
            self.level_circle.set_level(0.0)

    def _update_stats(self):
        self.stats_label.text = f'录制:{self._rec_count}  播放:{self._play_count}'

    def _show_error(self, msg):
        self._enter_state(STATE_ERROR)
        self._set_status(f'[color=E74C3C]{msg}[/color]')

    def _show_toast(self, msg):
        self._set_status(msg)

    # ===================== 时钟 =====================
    def _tick(self, dt):
        # 按下时长到触发阈值
        if self.state == STATE_PRESSING and self._press_started_at:
            if time.time() - self._press_started_at >= LONG_PRESS_TRIGGER:
                self._start_recording()
                return
        # 录音中：刷新电平 + 检查时长上限
        if self.state == STATE_RECORDING:
            level = self.recorder.get_current_level()
            if level is not None:
                self.level_circle.set_level(level)
            elapsed = time.time() - (self._record_started_at or time.time())
            self._set_status(f'● 录音中 {elapsed:.1f}s')
            if elapsed >= MAX_DURATION_SEC:
                self._max_duration_hit.set()
                if self._cancelling:
                    self._cancel_recording()
                else:
                    self._stop_recording_and_play()

    def _cleanup_stale_files(self):
        """启动时清理上一次会话残留的录音文件"""
        try:
            folder = os.path.join(get_app_dir(), 'recordings')
            if os.path.isdir(folder):
                for name in os.listdir(folder):
                    full = os.path.join(folder, name)
                    try:
                        os.remove(full)
                    except Exception:
                        pass
        except Exception:
            pass

    def on_stop(self):
        # App 退出时确保清理
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
        self._cleanup_stale_files()


from _controller import EchoController  # noqa: F401

if __name__ == '__main__':
    EchoPressApp().run()
