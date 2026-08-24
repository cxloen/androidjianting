# EchoPress

> 按住录音，松手回放，回放完自动删除。
> 不留下任何录音文件，按下开口，松手即听，听完全自动消失。

## 行为

- 按住中央大圆按钮 → 录音开始（带 0.2s 防误触）
- 录音时上滑 → 提示"松开取消"，松手后丢弃不保留
- 松手 → 立即进入回放
- 回放结束 → 自动删除录音文件，回到"准备就绪"
- 单次录音上限 60 秒；超过 0.2s 才算有效录音
- App 启动时自动清理上次会话的残留录音

## 平台支持

| 平台 | 录音 | 回放 |
| --- | --- | --- |
| Android 真机 | `MediaRecorder` (AAC / m4a) | `MediaPlayer` |
| 桌面 (Win/Mac/Linux) 调试 | `sounddevice` | `sounddevice` + `scipy.io.wavfile` |

## 项目结构

```
androidjianting/
├── main.py             # Kivy App 入口 + UI
├── _constants.py       # 共享常量与工具函数（采样率/阈值/状态名/safe_remove/路径工厂）
├── _controller.py      # EchoController 纯逻辑状态机（无 UI 依赖）
├── _constants.py       # 共享常量与工具函数
├── buildozer.spec      # Android 构建配置
├── test_echo.py        # headless 状态机测试（13 用例）
└── README.md
```

## 运行测试（headless，不启动 GUI）

```powershell
python test_echo.py
```

13 个用例覆盖：初始状态、防误触、完整录音→回放→删除、上滑取消、太短丢弃、回放中打断、
时长上限、Recorder 启动失败、Player 启动失败、shutdown 清理、统计累加、
取消提示、电平回调。

## 桌面调试

```powershell
pip install kivy sounddevice numpy scipy
python main.py
```

## Android 打包

```powershell
buildozer android debug
# 产物: bin/echopress-1.0.0-debug.apk
```

要求 Android 5.0+ (API 21),目标 API 33。仅需 `RECORD_AUDIO` 权限。
