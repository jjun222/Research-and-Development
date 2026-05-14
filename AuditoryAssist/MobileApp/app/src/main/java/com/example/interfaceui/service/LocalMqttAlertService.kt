package com.example.interfaceui.service

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.IBinder
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import com.example.interfaceui.MainActivity
import com.example.interfaceui.MqttHelper
import com.example.interfaceui.R
import com.example.interfaceui.broker.BrokerBootstrap
import com.example.interfaceui.data.AppDatabase
import com.example.interfaceui.data.NotificationEntity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONObject
import kotlinx.coroutines.cancel

class LocalMqttAlertService : Service() {

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val mqttListener: (String, String) -> Unit = { topic, payload ->
        if (isAlertTopic(topic)) {
            handleAlertMessage(topic, payload)
        }
    }

    override fun onCreate() {
        super.onCreate()
        createChannels()
        startForeground(FOREGROUND_ID, buildServiceNotification())
        startMqtt()
    }

    override fun onDestroy() {
        MqttHelper.instance?.removeMessageListener(mqttListener)
        serviceScope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startMqtt() {
        BrokerBootstrap.prepare(applicationContext) { result ->
            if (!result.connected) {
                showSystemNotification(
                    title = "MQTT 연결 실패",
                    body = result.errorMessage ?: "로컬 알림 서비스를 시작하지 못했습니다."
                )
                return@prepare
            }

            val helper = MqttHelper.instance ?: return@prepare

            helper.addMessageListener(mqttListener)

            // 판단 서버에서 앱 알림용으로 보내는 권장 토픽
            helper.subscribe("alerts/#", qos = 1)

            // 기존 센서 토픽도 임시 호환용으로 구독 가능
            helper.subscribe("shz/sensor", qos = 1)
            helper.subscribe("mq7/sensor", qos = 1)
            helper.subscribe("gas/sensor", qos = 1)
            helper.subscribe("AI_fire_alert", qos = 1)
            helper.subscribe("water_level/sensor", qos = 1)
            helper.subscribe("doorbell/sensor", qos = 1)

            PushTokenRegistrar.flushPendingToken(applicationContext)
        }
    }

    private fun isAlertTopic(topic: String): Boolean {
        return topic.startsWith("alerts/") ||
                topic == "shz/sensor" ||
                topic == "mq7/sensor" ||
                topic == "gas/sensor" ||
                topic == "AI_fire_alert" ||
                topic == "water_level/sensor" ||
                topic == "doorbell/sensor"
    }

    private fun handleAlertMessage(topic: String, payload: String) {
        val parsed = parseAlert(topic, payload)

        serviceScope.launch {
            AppDatabase.getDatabase(applicationContext)
                .notificationDao()
                .insert(
                    NotificationEntity(
                        title = parsed.title,
                        message = parsed.body,
                        createdAt = parsed.timestampMs
                    )
                )
        }

        showSystemNotification(parsed.title, parsed.body)

        if (parsed.level == "critical" || parsed.type == "fire") {
            showEmergencyNotification(parsed.title, parsed.body)
        }
    }

    private fun parseAlert(topic: String, payload: String): ParsedAlert {
        return runCatching {
            val json = JSONObject(payload)

            val type = json.optString("type").ifBlank {
                when (topic) {
                    "shz/sensor" -> "flame"
                    "mq7/sensor" -> "co"
                    "gas/sensor" -> "gas"
                    "AI_fire_alert" -> "fire"
                    "water_level/sensor" -> "water"
                    "doorbell/sensor" -> "doorbell"
                    else -> "system"
                }
            }

            val title = json.optString("title").ifBlank {
                when (type) {
                    "fire" -> "화재 감지"
                    "flame" -> "불꽃 감지"
                    "gas" -> "가스 감지"
                    "co" -> "일산화탄소 감지"
                    "water" -> "수위 감지"
                    "doorbell" -> "초인종 감지"
                    else -> "시스템 알림"
                }
            }

            val body = json.optString("body").ifBlank {
                json.optString("message").ifBlank {
                    when (type) {
                        "fire" -> "화재 위험 신호가 감지되었습니다."
                        "flame" -> "불꽃 센서가 감지되었습니다."
                        "gas" -> "가스 센서가 위험 상태를 감지했습니다."
                        "co" -> "일산화탄소 센서가 위험 상태를 감지했습니다."
                        "water" -> "수위 센서가 감지되었습니다."
                        "doorbell" -> "초인종 버튼이 감지되었습니다."
                        else -> payload
                    }
                }
            }

            ParsedAlert(
                type = type,
                level = json.optString("level", if (type == "fire") "critical" else "warning"),
                title = title,
                body = body,
                timestampMs = json.optLong("timestamp_ms", System.currentTimeMillis())
            )
        }.getOrElse {
            ParsedAlert(
                type = "system",
                level = "info",
                title = "MQTT 알림",
                body = payload,
                timestampMs = System.currentTimeMillis()
            )
        }
    }

    private fun createChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return

        val manager = getSystemService(NotificationManager::class.java)

        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_SERVICE,
                "로컬 MQTT 연결 상태",
                NotificationManager.IMPORTANCE_LOW
            )
        )

        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ALERT,
                "위험 알림",
                NotificationManager.IMPORTANCE_HIGH
            )
        )
    }

    private fun buildServiceNotification() =
        NotificationCompat.Builder(this, CHANNEL_SERVICE)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("AuditoryAssist 로컬 알림 실행 중")
            .setContentText("인터넷이 없어도 MQTT 알림을 수신합니다.")
            .setOngoing(true)
            .build()

    private fun showSystemNotification(title: String, body: String) {
        if (!canPostNotification()) return

        val intent = Intent(this, MainActivity::class.java)
        val pendingIntent = PendingIntent.getActivity(
            this,
            100,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification = NotificationCompat.Builder(this, CHANNEL_ALERT)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .build()

        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(System.currentTimeMillis().toInt(), notification)
    }

    private fun showEmergencyNotification(title: String, body: String) {
        // 1차 구현에서는 고우선순위 알림만 띄운다.
        // 다음 단계에서 EmergencyAlertActivity를 fullScreenIntent로 연결하면 된다.
        showSystemNotification("긴급: $title", body)
    }

    private fun canPostNotification(): Boolean {
        if (Build.VERSION.SDK_INT < 33) return true

        return ActivityCompat.checkSelfPermission(
            this,
            Manifest.permission.POST_NOTIFICATIONS
        ) == PackageManager.PERMISSION_GRANTED
    }

    data class ParsedAlert(
        val type: String,
        val level: String,
        val title: String,
        val body: String,
        val timestampMs: Long
    )

    companion object {
        private const val FOREGROUND_ID = 2001

        private const val CHANNEL_SERVICE = "local_mqtt_service"
        private const val CHANNEL_ALERT = "local_mqtt_alerts"

        fun start(context: Context) {
            val intent = Intent(context, LocalMqttAlertService::class.java)

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, LocalMqttAlertService::class.java))
        }
    }
}
