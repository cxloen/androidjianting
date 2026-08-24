[app]

# 应用名称
title = EchoPress

# 包名
package.name = echopress

# 包域名
package.domain = org.teleagent

# 源码目录
source.dir = .

# 源码包含扩展名
source.include_exts = py,png,jpg,kv,atlas,ttf,otf

# 应用版本
version = 1.0.0

# 最低Android SDK版本
android.minapi = 21

# 目标Android SDK版本
android.api = 33

# Android NDK版本
android.ndk = 25b

# 架构
android.archs = arm64-v8a, armeabi-v7a

# 权限（按住录音）
android.permissions = RECORD_AUDIO

# Android前台服务（Android 9+后台录音需要，本应用只在按下时录音，可不开启）
android.foreground_services = false

# 自动接受SDK许可证
android.accept_sdk_license = True

# Java类依赖
android.gradle_dependencies =

# p4a 仓库（CI 跑在 GitHub 上，直接用官方仓库；用稳定 tag 不用 develop）
p4a.url = https://github.com/kivy/python-for-android.git
p4a.branch = v2024.01.21

# 启动模式
android.allow_backup = False

# 图标（如需要可替换）
# icon.filename = %(source.dir)s/icon.png

# 全屏
fullscreen = 0

# 屏幕方向
orientation = portrait

# 需求
requirements = python3,kivy,pyjnius

# 日志级别
log_level = 2

# 使用presplash
# presplash.filename = %(source.dir)s/presplash.png

# Android入口类
android.entrypoint = org.kivy.android.PythonActivity

# 启动时复制assets
# android.add_src =

# 私有存储
android.private_storage = True

# 各权限声明
[app:android.permissions]
RECORD_AUDIO = 用于按住录音并立即回放

[buildozer]

# 构建目录
build.dir = ./.buildozer

# 构建日志级别
log_level = 2

# 构建警告视为错误
warn_on_buildozer_version = 1
