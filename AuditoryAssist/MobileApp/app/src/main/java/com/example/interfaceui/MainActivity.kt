package com.example.interfaceui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Bundle
import android.widget.TextView
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.NotificationManagerCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.example.interfaceui.broker.BrokerBootstrap
import com.example.interfaceui.net.CameraRegistryStore
import com.example.interfaceui.service.LocalMqttAlertService
import com.example.interfaceui.service.PushAlertService
import com.example.interfaceui.ui1.CheckActivity
import com.example.interfaceui.ui1.DeviceSelectActivity
import com.example.interfaceui.ui1.LiveVideoActivity
import com.example.interfaceui.ui1.LogActivity
import com.example.interfaceui.ui1.NotificationActivity
import com.example.interfaceui.ui1.SettingActivity
import com.example.interfaceui.ui1.SituationStatusActivity
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity() {
    private lateinit var tvMqttStatus: TextView
    private lateinit var tvBrokerUri: TextView
    private lateinit var tvAlertMode: TextView
    private lateinit var tvLastReceived: TextView

    private var isScreenActive = false
    private var lastConnectedUri: String? = null

    private val mainMqttListener: (String, String) -> Unit = listener@{ topic, payload ->
        // AI 카메라가 MQTT registry/status로 video_url을 publish하면 앱 foreground에서도 저장한다.
        CameraRegistryStore.handleMqttMessage(applicationContext, topic, payload)

        if (!isScreenActive) return@listener
        if (!shouldUpdateLastReceived(topic)) return@listener

        runOnUiThread {
            if (isScreenActive) {
                tvLastReceived.text = "마지막 수신: ${nowText()}"
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_main)

        val root = findViewById<android.view.View>(R.id.root)
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            val sysBars = insets.getInsets(WindowInsetsCompat.Type.statusBars())
            v.updatePadding(top = sysBars.top)
            insets
        }

        tvMqttStatus = findViewById(R.id.tvMqttStatus)
        tvBrokerUri = findViewById(R.id.tvBrokerUri)
        tvAlertMode = findViewById(R.id.tvAlertMode)
        tvLastReceived = findViewById(R.id.tvLastReceived)

        bindMenuButtons()
        requestPostNotificationIfNeeded()
        showInitialStatus()
        connectAndStartServices()
    }

    override fun onStart() {
        super.onStart()
        isScreenActive = true

        MqttHelper.instance?.let { helper ->
            helper.removeMessageListener(mainMqttListener)
            helper.addMessageListener(mainMqttListener)
            subscribeHomeTopics(helper)
        }

        refreshStatusCardFromCurrentState()
    }

    override fun onStop() {
        isScreenActive = false
        MqttHelper.instance?.removeMessageListener(mainMqttListener)
        super.onStop()
    }

    private fun bindMenuButtons() {
        findViewById<android.view.View>(R.id.btnMoveSituationStatus).setOnClickListener {
            startActivity(Intent(this, SituationStatusActivity::class.java))
        }

        findViewById<android.view.View>(R.id.btnMoveCheck).setOnClickListener {
            startActivity(Intent(this, CheckActivity::class.java))
        }

        findViewById<android.view.View>(R.id.btnMoveLog).setOnClickListener {
            startActivity(Intent(this, LogActivity::class.java))
        }

        findViewById<android.view.View>(R.id.btnMoveSetting).setOnClickListener {
            startActivity(Intent(this, SettingActivity::class.java))
        }

        findViewById<android.view.View>(R.id.btnMoveCamera).setOnClickListener {
            startActivity(Intent(this, LiveVideoActivity::class.java))
        }

        findViewById<android.view.View>(R.id.btnMoveDevice).setOnClickListener {
            startActivity(Intent(this, DeviceSelectActivity::class.java))
        }

        findViewById<android.view.View>(R.id.btnMoveNotification).setOnClickListener {
            startActivity(Intent(this, NotificationActivity::class.java))
        }

        findViewById<android.view.View>(R.id.btnMoveWifiSettings).setOnClickListener {
            startActivity(Intent(this, WifiSettingsActivity::class.java))
        }
    }

    private fun showInitialStatus() {
        tvMqttStatus.text = "MQTT 상태: 연결 준비 중"
        tvBrokerUri.text = "브로커: 확인 중..."
        tvAlertMode.text = "알림 방식: 확인 중..."
        tvLastReceived.text = "마지막 수신: 없음"
    }

    private fun connectAndStartServices() {
        tvMqttStatus.text = "MQTT 상태: 연결 중..."
        tvBrokerUri.text = "브로커: 검색 중..."
        tvAlertMode.text = "알림 방식: 확인 중..."

        BrokerBootstrap.prepare(applicationContext) { result ->
            runOnUiThread {
                if (isFinishing || isDestroyed) return@runOnUiThread

                if (result.connected) {
                    lastConnectedUri = result.uri

                    tvMqttStatus.text = if (result.discovered) {
                        "MQTT 상태: 연결됨 / 자동 검색"
                    } else {
                        "MQTT 상태: 연결됨 / 저장된 브로커"
                    }

                    tvBrokerUri.text = "브로커: ${result.uri ?: "알 수 없음"}"
                    tvAlertMode.text = buildAlertModeText()

                    val helper = MqttHelper.instance
                    if (helper != null) {
                        helper.removeMessageListener(mainMqttListener)
                        helper.addMessageListener(mainMqttListener)
                        subscribeHomeTopics(helper)
                        requestRegistryHello(helper)
                    }

                    PushAlertService.ensureTokenRegistered(applicationContext)
                    LocalMqttAlertService.start(applicationContext)
                } else {
                    lastConnectedUri = null
                    tvMqttStatus.text = "MQTT 상태: 연결 실패"
                    tvBrokerUri.text = "브로커: 없음"
                    tvAlertMode.text = if (isInternetAvailable()) {
                        "알림 방식: MQTT 설정 필요 / Firebase 대기"
                    } else {
                        "알림 방식: 로컬 MQTT 설정 필요 / 인터넷 없음"
                    }
                }
            }
        }
    }

    private fun refreshStatusCardFromCurrentState() {
        val uri = lastConnectedUri ?: MqttHelper.instance?.currentServerUri

        if (!uri.isNullOrBlank()) {
            tvBrokerUri.text = "브로커: $uri"
            if (!tvMqttStatus.text.contains("연결됨")) {
                tvMqttStatus.text = "MQTT 상태: 연결 정보 있음"
            }
            tvAlertMode.text = buildAlertModeText()
        }
    }

    private fun subscribeHomeTopics(helper: MqttHelper) {
        listOf(
            "alerts/#",
            "interfaceui/status/server",
            "interfaceui/registry/hello/#",
            "interfaceui/status/publisher/AI_D_fire",
            "shz/sensor",
            "mq7/sensor",
            "gas/sensor",
            "AI_fire_alert",
            "water_level/sensor",
            "doorbell/sensor"
        ).forEach { topic ->
            helper.subscribe(topic, qos = 1)
        }
    }

    private fun requestRegistryHello(helper: MqttHelper) {
        val payload = org.json.JSONObject()
            .put("request", "hello")
            .put("source", "android_main")
            .put("ts_ms", System.currentTimeMillis())
            .toString()

        helper.publish(
            topic = "interfaceui/registry/request",
            payload = payload,
            qos = 1,
            retain = false
        )
    }

    private fun shouldUpdateLastReceived(topic: String): Boolean {
        return topic.startsWith("alerts/") ||
            topic.startsWith("interfaceui/registry/hello/") ||
            topic == "interfaceui/status/server" ||
            topic == "interfaceui/status/publisher/AI_D_fire" ||
            topic == "shz/sensor" ||
            topic == "mq7/sensor" ||
            topic == "gas/sensor" ||
            topic == "AI_fire_alert" ||
            topic == "water_level/sensor" ||
            topic == "doorbell/sensor"
    }

    private fun buildAlertModeText(): String {
        return if (isInternetAvailable()) {
            "알림 방식: 로컬 MQTT + Firebase"
        } else {
            "알림 방식: 로컬 MQTT / Firebase 대기"
        }
    }

    private fun requestPostNotificationIfNeeded() {
        if (Build.VERSION.SDK_INT >= 33) {
            val enabled = NotificationManagerCompat.from(this).areNotificationsEnabled()
            if (!enabled) {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
            }
        }
    }

    private fun isInternetAvailable(): Boolean {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val network = cm.activeNetwork ?: return false
        val capabilities = cm.getNetworkCapabilities(network) ?: return false

        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    }

    private fun nowText(): String {
        return SimpleDateFormat("HH:mm:ss", Locale.KOREA).format(Date())
    }
}
