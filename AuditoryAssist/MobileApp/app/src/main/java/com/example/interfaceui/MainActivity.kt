package com.example.interfaceui

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.NotificationManagerCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.example.interfaceui.broker.BrokerBootstrap
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

    private val mainMqttListener: (String, String) -> Unit = { topic, _ ->
        if (
            topic.startsWith("alerts/") ||
            topic == "shz/sensor" ||
            topic == "mq7/sensor" ||
            topic == "gas/sensor" ||
            topic == "AI_fire_alert" ||
            topic == "water_level/sensor" ||
            topic == "doorbell/sensor"
        ) {
            runOnUiThread {
                tvLastReceived.text = "마지막 수신: ${nowText()}"
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        enableEdgeToEdge()
        setContentView(R.layout.activity_main)

        val root = findViewById<View>(R.id.root)
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
        connectAndStartServices()
    }

    override fun onDestroy() {
        MqttHelper.instance?.removeMessageListener(mainMqttListener)
        super.onDestroy()
    }

    private fun bindMenuButtons() {
        findViewById<View>(R.id.btnMoveSituationStatus).setOnClickListener {
            startActivity(Intent(this, SituationStatusActivity::class.java))
        }

        findViewById<View>(R.id.btnMoveCheck).setOnClickListener {
            startActivity(Intent(this, CheckActivity::class.java))
        }

        findViewById<View>(R.id.btnMoveLog).setOnClickListener {
            startActivity(Intent(this, LogActivity::class.java))
        }

        findViewById<View>(R.id.btnMoveSetting).setOnClickListener {
            startActivity(Intent(this, SettingActivity::class.java))
        }

        findViewById<View>(R.id.btnMoveCamera).setOnClickListener {
            startActivity(Intent(this, LiveVideoActivity::class.java))
        }

        findViewById<View>(R.id.btnMoveDevice).setOnClickListener {
            startActivity(Intent(this, DeviceSelectActivity::class.java))
        }

        findViewById<View>(R.id.btnMoveNotification).setOnClickListener {
            startActivity(Intent(this, NotificationActivity::class.java))
        }

        findViewById<View>(R.id.btnMoveWifiSettings).setOnClickListener {
            startActivity(Intent(this, WifiSettingsActivity::class.java))
        }
    }

    private fun connectAndStartServices() {
        tvMqttStatus.text = "MQTT 상태: 연결 중..."
        tvBrokerUri.text = "브로커: 검색 중..."
        tvAlertMode.text = "알림 방식: 확인 중..."

        BrokerBootstrap.prepare(applicationContext) { result ->
            runOnUiThread {
                if (result.connected) {
                    tvMqttStatus.text = "MQTT 상태: 연결됨"
                    tvBrokerUri.text = "브로커: ${result.uri ?: "알 수 없음"}"
                    tvAlertMode.text = "알림 방식: 로컬 MQTT + Firebase"

                    val helper = MqttHelper.instance
                    if (helper != null) {
                        helper.removeMessageListener(mainMqttListener)
                        helper.addMessageListener(mainMqttListener)
                    }

                    PushAlertService.ensureTokenRegistered(applicationContext)
                    LocalMqttAlertService.start(applicationContext)
                } else {
                    tvMqttStatus.text = "MQTT 상태: 연결 실패"
                    tvBrokerUri.text = "브로커: 없음"
                    tvAlertMode.text = "알림 방식: Firebase만 가능하거나 설정 필요"
                }
            }
        }
    }

    private fun requestPostNotificationIfNeeded() {
        if (Build.VERSION.SDK_INT >= 33) {
            val enabled = NotificationManagerCompat.from(this).areNotificationsEnabled()

            if (!enabled) {
                requestPermissions(
                    arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                    1001
                )
            }
        }
    }

    private fun nowText(): String {
        return SimpleDateFormat("HH:mm:ss", Locale.KOREA).format(Date())
    }
}
