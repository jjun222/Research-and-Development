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
import android.text.InputType
import android.widget.EditText
import android.widget.Toast
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.dialog.MaterialAlertDialogBuilder

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

        bindDeveloperModeToolbar()
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

    private fun bindDeveloperModeToolbar() {
        val toolbar = findViewById<MaterialToolbar>(R.id.toolbar)

        toolbar.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                R.id.menuDeveloperMode -> {
                    showDeveloperPasswordDialog()
                    true
                }
                else -> false
            }
        }
    }

    private fun showDeveloperPasswordDialog() {
        val passwordInput = EditText(this).apply {
            hint = "비밀번호 입력"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
            setSingleLine(true)
            setPadding(48, 24, 48, 24)
        }

        MaterialAlertDialogBuilder(this)
            .setTitle("개발자 모드")
            .setMessage("개발자 모드 비밀번호를 입력하세요.")
            .setView(passwordInput)
            .setNegativeButton("취소", null)
            .setPositiveButton("확인") { _, _ ->
                val password = passwordInput.text.toString().trim()

                if (password == DEVELOPER_MODE_PASSWORD) {
                    showDeveloperModeMenu()
                } else {
                    Toast.makeText(this, "비밀번호가 올바르지 않습니다.", Toast.LENGTH_SHORT).show()
                }
            }
            .show()
    }

    private fun showDeveloperModeMenu() {
        val menuItems = arrayOf(
            "기기 로그",
            "MQTT / Wi-Fi 설정",
            "기기 등록 및 삭제",
            "상태 확인"
        )

        MaterialAlertDialogBuilder(this)
            .setTitle("개발자 모드")
            .setItems(menuItems) { _, which ->
                when (which) {
                    0 -> startActivity(Intent(this, LogActivity::class.java))
                    1 -> startActivity(Intent(this, WifiSettingsActivity::class.java))
                    2 -> startActivity(Intent(this, DeviceSelectActivity::class.java))
                    3 -> startActivity(Intent(this, SituationStatusActivity::class.java))
                }
            }
            .setNegativeButton("닫기", null)
            .show()
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

    companion object {
        private const val DEVELOPER_MODE_PASSWORD = "1234"
    }
}
