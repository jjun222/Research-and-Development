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
import com.example.interfaceui.net.MjpegReader
import com.google.android.material.appbar.MaterialToolbar
import kotlinx.coroutines.Job
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.system.measureTimeMillis

class LiveVideoActivity : AppCompatActivity() {

    private val streamUrl = "http://192.168.0.57:5055/video"

    private lateinit var img: ImageView
    private lateinit var tvStatus: TextView
    private lateinit var tvFps: TextView

    private var streamJob: Job? = null
    private val stopping = AtomicBoolean(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_live_video)

        // 세로 고정
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        img = findViewById(R.id.imgFrame)
        tvStatus = findViewById(R.id.tvStatus)
        tvFps = findViewById(R.id.tvFps)

        findViewById<MaterialToolbar>(R.id.toolbar)?.setNavigationOnClickListener {
            onBackPressedDispatcher.onBackPressed()
        }
        findViewById<android.widget.Button>(R.id.btnReconnect).setOnClickListener {
            restartStream()
        }
    }

    override fun onStart() {
        super.onStart()
        startStream()
    }

    override fun onStop() {
        super.onStop()
        stopStream()
    }

    private fun startStream() {
        if (streamJob?.isActive == true) return
        stopping.set(false)

        tvStatus.text = "연결 중… ($streamUrl)"
        val reader = MjpegReader()

        streamJob = lifecycleScope.launch(Dispatchers.IO) {
            var lastTs = System.nanoTime()
            try {
                reader.read(
                    url = streamUrl,
                    onFrame = { bmp: Bitmap ->
                        val elapsed = measureTimeMillis {
                            lifecycleScope.launch(Dispatchers.Main) {
                                img.setImageBitmap(bmp)  // XML에서 4:3 비율 고정 + fitCenter
                                val now = System.nanoTime()
                                val fps = 1_000_000_000.0 / (now - lastTs).coerceAtLeast(1)
                                tvFps.text = "FPS: ${"%.2f".format(fps)}"
                                lastTs = now
                            }
                        }
                        if (elapsed > 30) { /* 필요 시 샘플링 */ }
                    },
                    onConnected = {
                        lifecycleScope.launch(Dispatchers.Main) { tvStatus.text = "연결됨" }
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
            } catch (_: Exception) { /* onDisconnected에서 처리 */ }
        }
    }

    private fun stopStream() {
        stopping.set(true)
        val job = streamJob ?: return
        streamJob = null
        lifecycleScope.launch { try { job.cancelAndJoin() } catch (_: Exception) {} }
    }

    private fun restartStream() {
        stopStream()
        startStream()
    }
}
