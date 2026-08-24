"""EchoPress 共享常量。"""
SAMPLE_RATE = 44100
MAX_DURATION_SEC = 60
MIN_DURATION_SEC = 0.2
LONG_PRESS_TRIGGER = 0.2
CANCEL_SLIDE_PX = 80

# 状态机
STATE_IDLE = 'idle'
STATE_PRESSING = 'pressing'
STATE_RECORDING = 'recording'
STATE_CANCELLING = 'cancelling'
STATE_PLAYING = 'playing'
STATE_ERROR = 'error'
def safe_remove(path):
    try:
        if path and __import__("os").path.exists(path):
            __import__("os").remove(path)
    except Exception as e:
        print("[WARN] delete failed:", e)

def get_recording_path():
    import os, time
    try:
        from kivy.app import App
        base = App.get_running_app().user_data_dir
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".echopress_tmp")
    folder = os.path.join(base, "recordings")
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        pass
    ts = int(time.time() * 1000)
    ext = "m4a" if __import__("sys").platform != "win32" and __import__("os").name != "nt" else "wav"
    return os.path.join(folder, f"rec_{ts}.{ext}")
