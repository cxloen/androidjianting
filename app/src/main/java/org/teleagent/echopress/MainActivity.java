package org.teleagent.echopress;

import android.app.Activity;
import android.content.pm.PackageManager;
import android.media.MediaPlayer;
import android.media.MediaRecorder;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.MotionEvent;
import android.view.View;
import android.widget.TextView;

import java.io.File;
import java.util.Locale;

/**
 * EchoPress - 按住录音，松手回放，回放完自动删除。
 *
 * 交互逻辑：
 * 1. 按下大圆按钮 -> 等待 200ms 防误触 -> 开始录音
 * 2. 录音中上滑 80px -> 标记取消
 * 3. 松手 -> 如果取消则丢弃；如果时长 < 0.2s 提示太短；否则自动回放
 * 4. 回放完 -> 自动删除录音文件
 * 5. 回放中按下 -> 打断回放，开始新一轮
 */
public class MainActivity extends Activity {

    // 常量（与原 Python 版保持一致）
    private static final int SAMPLE_RATE = 44100;
    private static final int MAX_DURATION_SEC = 60;
    private static final long MIN_DURATION_MS = 200;
    private static final long LONG_PRESS_TRIGGER_MS = 200;
    private static final float CANCEL_SLIDE_PX = 80f;
    private static final int REQUEST_RECORD_AUDIO = 100;

    // 状态机
    private static final int STATE_IDLE = 0;
    private static final int STATE_PRESSING = 1;
    private static final int STATE_RECORDING = 2;
    private static final int STATE_PLAYING = 3;

    private int state = STATE_IDLE;
    private MediaRecorder recorder;
    private MediaPlayer player;
    private File recordedFile;

    private long pressDownTime;
    private long recordStartTime;
    private float touchStartY;
    private boolean cancelling;

    private int recCount = 0;
    private int playCount = 0;

    private TextView statusText;
    private TextView statsText;
    private View bigButton;
    private View levelCircle;

    private final Handler handler = new Handler(Looper.getMainLooper());

    // 录音电平刷新 Runnable
    private final Runnable levelUpdater = new Runnable() {
        @Override
        public void run() {
            if (state == STATE_RECORDING && recorder != null) {
                try {
                    int amp = recorder.getMaxAmplitude();
                    float level = Math.min(1f, amp / 8000f);
                    updateCircle(level);
                    long elapsed = (System.currentTimeMillis() - recordStartTime) / 1000;
                    setStatus("● 录音中 " + elapsed + "s");
                    // 超过最大时长自动停止
                    if (System.currentTimeMillis() - recordStartTime >= MAX_DURATION_SEC * 1000L) {
                        if (cancelling) {
                            cancelRecording();
                        } else {
                            stopRecordingAndPlay();
                        }
                        return;
                    }
                    handler.postDelayed(this, 50);
                } catch (Exception e) {
                    // recorder 已释放，忽略
                }
            }
        }
    };

    // 防误触触发 Runnable
    private final Runnable longPressRunnable = new Runnable() {
        @Override
        public void run() {
            if (state == STATE_PRESSING) {
                startRecording();
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        statusText = findViewById(R.id.status_text);
        statsText = findViewById(R.id.stats_text);
        bigButton = findViewById(R.id.big_button);
        levelCircle = findViewById(R.id.level_circle);

        // 清理上次残留
        cleanupStaleFiles();

        // 请求录音权限
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                    new String[]{android.Manifest.permission.RECORD_AUDIO},
                    REQUEST_RECORD_AUDIO);
        }

        // 触摸事件
        bigButton.setOnTouchListener(new View.OnTouchListener() {
            @Override
            public boolean onTouch(View v, MotionEvent event) {
                switch (event.getAction()) {
                    case MotionEvent.ACTION_DOWN:
                        onTouchDown(event);
                        return true;
                    case MotionEvent.ACTION_MOVE:
                        onTouchMove(event);
                        return true;
                    case MotionEvent.ACTION_UP:
                    case MotionEvent.ACTION_CANCEL:
                        onTouchUp();
                        return true;
                }
                return false;
            }
        });

        updateStats();
    }

    // ===================== 触摸处理 =====================

    private void onTouchDown(MotionEvent event) {
        // 回放中按下 -> 打断并开始新一轮
        if (state == STATE_PLAYING) {
            stopPlayback();
            safeDelete(recordedFile);
            recordedFile = null;
            enterState(STATE_IDLE);
        }
        if (state == STATE_IDLE) {
            touchStartY = event.getY();
            cancelling = false;
            pressDownTime = System.currentTimeMillis();
            enterState(STATE_PRESSING);
            handler.postDelayed(longPressRunnable, LONG_PRESS_TRIGGER_MS);
        }
    }

    private void onTouchMove(MotionEvent event) {
        if (state != STATE_RECORDING) return;
        float dy = event.getY() - touchStartY;
        if (dy < -CANCEL_SLIDE_PX && !cancelling) {
            cancelling = true;
            setStatus("松开取消");
        }
    }

    private void onTouchUp() {
        handler.removeCallbacks(longPressRunnable);
        if (state == STATE_PRESSING) {
            // 还没真正开始录音就松手
            enterState(STATE_IDLE);
        } else if (state == STATE_RECORDING) {
            if (cancelling) {
                cancelRecording();
            } else {
                stopRecordingAndPlay();
            }
        }
    }

    // ===================== 录音控制 =====================

    private void startRecording() {
        try {
            recordedFile = new File(getFilesDir(),
                    "rec_" + System.currentTimeMillis() + ".m4a");
            recorder = new MediaRecorder();
            recorder.setAudioSource(MediaRecorder.AudioSource.MIC);
            recorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
            recorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
            recorder.setAudioEncodingBitRate(64000);
            recorder.setAudioSamplingRate(SAMPLE_RATE);
            recorder.setOutputFile(recordedFile.getAbsolutePath());
            recorder.setMaxDuration(MAX_DURATION_SEC * 1000);
            recorder.prepare();
            recorder.start();
            recordStartTime = System.currentTimeMillis();
            enterState(STATE_RECORDING);
            handler.post(levelUpdater);
        } catch (Exception e) {
            showError("录音启动失败: " + e.getMessage());
            safeDelete(recordedFile);
            recordedFile = null;
        }
    }

    private void stopRecordingAndPlay() {
        long duration = System.currentTimeMillis() - recordStartTime;
        handler.removeCallbacks(levelUpdater);
        try {
            recorder.stop();
        } catch (Exception e) {
            // ignore
        }
        try {
            recorder.release();
        } catch (Exception e) {
            // ignore
        }
        recorder = null;

        if (duration < MIN_DURATION_MS) {
            setStatus("太短了");
            safeDelete(recordedFile);
            recordedFile = null;
            enterState(STATE_IDLE);
            return;
        }

        recCount++;
        updateStats();
        startPlayback(recordedFile);
    }

    private void cancelRecording() {
        handler.removeCallbacks(levelUpdater);
        try {
            recorder.stop();
        } catch (Exception e) {
            // ignore
        }
        try {
            recorder.release();
        } catch (Exception e) {
            // ignore
        }
        recorder = null;
        safeDelete(recordedFile);
        recordedFile = null;
        enterState(STATE_IDLE);
    }

    // ===================== 回放 =====================

    private void startPlayback(final File file) {
        enterState(STATE_PLAYING);
        try {
            player = new MediaPlayer();
            player.setDataSource(file.getAbsolutePath());
            player.prepare();
            player.setOnCompletionListener(new MediaPlayer.OnCompletionListener() {
                @Override
                public void onCompletion(MediaPlayer mp) {
                    onPlayComplete(file);
                }
            });
            player.start();
        } catch (Exception e) {
            showError("回放失败: " + e.getMessage());
            safeDelete(file);
            if (file == recordedFile) recordedFile = null;
            enterState(STATE_IDLE);
        }
    }

    private void onPlayComplete(File file) {
        // 核心：放完自动删除
        safeDelete(file);
        if (file == recordedFile) recordedFile = null;
        playCount++;
        updateStats();
        enterState(STATE_IDLE);
    }

    private void stopPlayback() {
        if (player != null) {
            try {
                if (player.isPlaying()) player.stop();
            } catch (Exception e) {
                // ignore
            }
            try {
                player.release();
            } catch (Exception e) {
                // ignore
            }
            player = null;
        }
    }

    // ===================== 状态/UI =====================

    private void enterState(int newState) {
        state = newState;
        switch (newState) {
            case STATE_IDLE:
                setStatus("准备就绪");
                updateCircle(0.05f);
                break;
            case STATE_PRESSING:
                setStatus("按下中...");
                updateCircle(0.2f);
                break;
            case STATE_RECORDING:
                setStatus("● 录音中");
                break;
            case STATE_PLAYING:
                setStatus("▶ 回放中");
                updateCircle(0.7f);
                break;
        }
    }

    private void setStatus(String msg) {
        statusText.setText(msg);
    }

    private void updateStats() {
        statsText.setText(String.format(Locale.getDefault(),
                "录制:%d  播放:%d", recCount, playCount));
    }

    private void showError(String msg) {
        statusText.setText(msg);
        updateCircle(0.0f);
    }

    private void updateCircle(float level) {
        // 通过 alpha 和 scale 简单表达电平
        levelCircle.setAlpha(0.3f + 0.7f * level);
        float scale = 0.85f + 0.3f * level;
        levelCircle.setScaleX(scale);
        levelCircle.setScaleY(scale);
    }

    // ===================== 工具 =====================

    private void safeDelete(File file) {
        try {
            if (file != null && file.exists()) file.delete();
        } catch (Exception e) {
            // ignore
        }
    }

    private void cleanupStaleFiles() {
        try {
            File dir = getFilesDir();
            for (File f : dir.listFiles()) {
                if (f.getName().startsWith("rec_")) f.delete();
            }
        } catch (Exception e) {
            // ignore
        }
    }

    // ===================== 生命周期 =====================

    @Override
    protected void onDestroy() {
        super.onDestroy();
        handler.removeCallbacks(levelUpdater);
        handler.removeCallbacks(longPressRunnable);
        if (recorder != null) {
            try { recorder.stop(); } catch (Exception e) {}
            try { recorder.release(); } catch (Exception e) {}
            recorder = null;
        }
        stopPlayback();
        safeDelete(recordedFile);
        recordedFile = null;
        cleanupStaleFiles();
    }
}
