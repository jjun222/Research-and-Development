package com.example.interfaceui.ui1

import android.content.pm.ActivityInfo
import android.graphics.Bitmap
import android.os.Bundle
import android.view.WindowManager
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.example.interfaceui.R
import com.example.interfaceui.broker.BrokerPrefs
import com.example.interfaceui.net.MjpegReader
import com.google.android.material.appbar.MaterialToolbar
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.system.measureTimeMillis

class LiveVideoActivity : AppCompatActivity() {
    private lateinit var img: ImageView
    private lateinit var tvStatus: TextView
    private lateinit var tvFps: TextView

    private var streamJob: Job? = null
    private val stopping = AtomicBoolean(false)

    private var streamUrl: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_live_video)

        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        img = findViewById(R.id.imgFrame)
        tvStatus = findViewById(R.id.tvStatus)
        tvFps = findViewById(R.id.tvFps)

        findViewById<MaterialToolbar>(R.id.toolbar)
            ?.setNavigationOnClickListener { onBackPressedDispatcher.onBackPressed() }

        findViewById<com.google.android.material.button.MaterialButton>(R.id.btnReconnect)
            .setOnClickListener { restartStream() }
    }

    override fun onStart() {
        super.onStart()
        startStream()
    }

    override fun onStop() {
        super.onStop()
        stopStream()
    }

    private fun resolveStreamUrl(): String? {
        // 1순위: Activity 호출자가 넘긴 URL
        val fromIntent = intent.getStringExtra(EXTRA_VIDEO_URL)
            ?.trim()
            ?.takeIf { it.startsWith("http://") || it.startsWith("https://") }

        if (!fromIntent.isNullOrBlank()) return fromIntent

        // 2순위: MQTT registry에서 저장된 AI 카메라 URL
        val saved = BrokerPrefs.getVideoUrlOrNull(applicationContext)
        if (!saved.isNullOrBlank()) return saved

        // 3순위: 제품 기본 hostname. IP가 아니라 AI 카메라 AP/Ubuntu hostname 기준이다.
        // AI 카메라 Pi에서 hostnamectl set-hostname ai-camera + avahi-daemon 설정이 되어 있어야 한다.
        return DEFAULT_AI_CAMERA_URL
    }

    private fun startStream() {
        if (streamJob?.isActive == true) return

        streamUrl = resolveStreamUrl()
        val url = streamUrl

        if (url.isNullOrBlank()) {
            tvStatus.text = "카메라 영상 주소가 설정되지 않았습니다."
            Toast.makeText(
                this,
                "AI 카메라 주소가 없습니다. MQTT registry 수신 또는 Wi-Fi 설정을 확인하세요.",
                Toast.LENGTH_SHORT
            ).show()
            return
        }

        stopping.set(false)
        tvStatus.text = "연결 중… ($url)"

        val reader = MjpegReader()

        streamJob = lifecycleScope.launch(Dispatchers.IO) {
            var lastTs = System.nanoTime()

            try {
                reader.read(
                    url = url,
                    onFrame = { bmp: Bitmap ->
                        val elapsed = measureTimeMillis {
                            lifecycleScope.launch(Dispatchers.Main) {
                                img.setImageBitmap(bmp)

                                val now = System.nanoTime()
                                val fps = 1_000_000_000.0 / (now - lastTs).coerceAtLeast(1)
                                tvFps.text = "FPS: ${"%.2f".format(fps)}"
                                lastTs = now
                            }
                        }

                        if (elapsed > 30) {
                            // 필요 시 frame 처리 지연 측정용으로 사용
                        }
                    },
                    onConnected = {
                        lifecycleScope.launch(Dispatchers.Main) {
                            tvStatus.text = "연결됨"
                        }
                    },
                    onDisconnected = { th ->
                        lifecycleScope.launch(Dispatchers.Main) {
                            tvStatus.text = "연결 끊김"

                            if (th != null) {
                                Toast.makeText(
                                    this@LiveVideoActivity,
                                    "스트림 오류: ${th.message ?: th.javaClass.simpleName}",
                                    Toast.LENGTH_SHORT
                                ).show()
                            }
                        }
                    },
                    cancelRequested = { stopping.get() || !isActive }
                )
            } catch (_: Exception) {
                // MjpegReader의 onDisconnected에서 화면 표시 처리
            }
        }
    }

    private fun stopStream() {
        stopping.set(true)

        val job = streamJob ?: return
        streamJob = null

        lifecycleScope.launch {
            try {
                job.cancelAndJoin()
            } catch (_: Exception) {
            }
        }
    }

    private fun restartStream() {
        stopStream()
        streamUrl = null
        startStream()
    }

    companion object {
        const val EXTRA_VIDEO_URL = "video_url"
        private const val DEFAULT_AI_CAMERA_URL = "http://ai-camera.local:5055/video"
    }
}
