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
import com.example.interfaceui.net.CameraRegistryStore
import com.example.interfaceui.ui1.EmergencyAlertActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import kotlin.math.abs

class LocalMqttAlertService : Service() {
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    @Volatile
    private var destroyed = false

    private var lastFailureNotificationAt = 0L

    /*
     * 화재 확정 all-True 판단용.
     *
     * 목적:
     * - 개별 센서 감지는 일반 알림만 표시
     * - 아래 4개가 15초 안에 모두 감지된 경우에만 빨간 긴급 화면 표시
     *
     * 필요한 4개:
     * - shz/sensor       : 불꽃 감지
     * - mq7/sensor       : 일산화탄소 감지
     * - gas/sensor       : 가스 감지
     * - AI_fire_alert    : AI 불 감지 카메라
     */
    private val fireDetectedAt = mutableMapOf(
        FIRE_KEY_FLAME to 0L,
        FIRE_KEY_CO to 0L,
        FIRE_KEY_GAS to 0L,
        FIRE_KEY_AI to 0L
    )

    private var allTrueActive = false
    private var lastEmergencyShownAt = 0L

    private val mqttListener: (String, String) -> Unit = listener@{ topic, payload ->
        // AI 카메라가 registry/status로 video_url을 보내면 백그라운드에서도 저장한다.
        if (CameraRegistryStore.handleMqttMessage(applicationContext, topic, payload)) {
            return@listener
        }

        if (isAlertTopic(topic)) {
            handleAlertMessage(topic, payload)
        }
    }

    override fun onCreate() {
        super.onCreate()
        destroyed = false
        createChannels()
        startForeground(FOREGROUND_ID, buildServiceNotification())
        startMqtt()
    }

    override fun onDestroy() {
        destroyed = true
        MqttHelper.instance?.removeMessageListener(mqttListener)
        serviceScope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startMqtt() {
        attemptMqttConnect()
    }

    private fun attemptMqttConnect() {
        if (destroyed) return

        BrokerBootstrap.prepare(applicationContext) { result ->
            if (destroyed) return@prepare

            if (!result.connected) {
                maybeShowConnectionFailure(result.errorMessage)
                scheduleReconnect()
                return@prepare
            }

            val helper = MqttHelper.instance ?: run {
                scheduleReconnect()
                return@prepare
            }

            helper.removeMessageListener(mqttListener)
            helper.addMessageListener(mqttListener)

            // 판단 서버에서 앱 알림용으로 보내는 권장 토픽
            helper.subscribe("alerts/#", qos = 1)

            // 기존 센서 raw topic 호환용
            helper.subscribe("shz/sensor", qos = 1)
            helper.subscribe("mq7/sensor", qos = 1)
            helper.subscribe("gas/sensor", qos = 1)
            helper.subscribe("AI_fire_alert", qos = 1)
            helper.subscribe("water_level/sensor", qos = 1)
            helper.subscribe("doorbell/sensor", qos = 1)

            // 혹시 기존/별칭 토픽이 들어오는 경우를 대비
            helper.subscribe("mq5/sensor", qos = 1)
            helper.subscribe("co/sensor", qos = 1)
            helper.subscribe("flame/sensor", qos = 1)
            helper.subscribe("AI_D_fire", qos = 1)

            // AI 카메라 video_url 자동 저장용
            helper.subscribe("interfaceui/registry/hello/#", qos = 1)
            helper.subscribe("interfaceui/status/publisher/AI_D_fire", qos = 1)

            // registry 재전송 요청. 판단 서버/장치가 지원하지 않아도 영향 없음.
            requestRegistryHello(helper)

            PushTokenRegistrar.flushPendingToken(applicationContext)
        }
    }

    private fun scheduleReconnect() {
        if (destroyed) return

        serviceScope.launch {
            delay(RECONNECT_DELAY_MS)
            if (!destroyed) {
                attemptMqttConnect()
            }
        }
    }

    private fun maybeShowConnectionFailure(errorMessage: String?) {
        val now = System.currentTimeMillis()
        if (now - lastFailureNotificationAt < FAILURE_NOTIFICATION_THROTTLE_MS) return

        lastFailureNotificationAt = now

        showSystemNotification(
            title = "MQTT 연결 실패",
            body = errorMessage ?: "로컬 알림 서비스가 MQTT Broker 연결을 재시도 중입니다."
        )
    }

    private fun requestRegistryHello(helper: MqttHelper) {
        val payload = JSONObject()
            .put("request", "hello")
            .put("source", "android_local_service")
            .put("ts_ms", System.currentTimeMillis())
            .toString()

        helper.publish(
            topic = "interfaceui/registry/request",
            payload = payload,
            qos = 1,
            retain = false
        )
    }

    private fun isAlertTopic(topic: String): Boolean {
        return topic.startsWith("alerts/") ||
            topic == "shz/sensor" ||
            topic == "mq7/sensor" ||
            topic == "gas/sensor" ||
            topic == "AI_fire_alert" ||
            topic == "water_level/sensor" ||
            topic == "doorbell/sensor" ||
            topic == "mq5/sensor" ||
            topic == "co/sensor" ||
            topic == "flame/sensor" ||
            topic == "AI_D_fire"
    }

    private fun handleAlertMessage(topic: String, payload: String) {
        /*
         * 순서 중요:
         * 1. raw 센서 메시지이면 15초 all-True 상태를 먼저 갱신
         * 2. 정상 메시지라면 parseAlertOrNull()이 null을 반환하므로 일반 알림도 띄우지 않음
         * 3. 개별 감지는 일반 알림만 표시
         * 4. all-True 또는 서버 fire_confirmed일 때만 빨간 긴급 화면 표시
         */
        val rawAllTrueDetected = updateFireAllTrueWindow(topic, payload)

        val parsed = parseAlertOrNull(topic, payload) ?: return

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

        val serverConfirmedAllTrue =
            isAllTrueFireAlert(topic, payload) && isFreshEmergencyPayload(payload)

        if (serverConfirmedAllTrue) {
            showFireConfirmedEmergency(
                title = parsed.title.ifBlank { FIRE_CONFIRMED_TITLE },
                body = parsed.body.ifBlank { FIRE_CONFIRMED_BODY },
                timestampMs = parsed.timestampMs
            )
            return
        }

        if (rawAllTrueDetected) {
            showFireConfirmedEmergency(
                title = FIRE_CONFIRMED_TITLE,
                body = FIRE_CONFIRMED_BODY,
                timestampMs = System.currentTimeMillis()
            )
        }
    }

    private fun parseAlertOrNull(topic: String, payload: String): ParsedAlert? {
        val fallbackType = typeFromTopic(topic)

        return try {
            val json = JSONObject(payload)

            if (isNormalPayload(json)) {
                return null
            }

            val type = json.optString("type").ifBlank { fallbackType }
            val isServerAlertTopic = topic.startsWith("alerts/")

            if (!isServerAlertTopic && !isDetectedPayload(topic, json)) {
                return null
            }

            val title = json.optString("title").ifBlank { titleForType(type) }
            val body = json.optString("body").ifBlank {
                json.optString("message").ifBlank { bodyForType(type) }
            }

            val timestampMs = payloadTimestampMsOrNull(json) ?: System.currentTimeMillis()

            ParsedAlert(
                type = type,
                level = json.optString("level", defaultLevelForType(type)),
                title = title,
                body = body,
                timestampMs = timestampMs
            )
        } catch (_: Exception) {
            parseTextAlertOrNull(topic, payload)
        }
    }

    private fun typeFromTopic(topic: String): String {
        return when (topic) {
            "shz/sensor", "flame/sensor" -> "flame"
            "mq7/sensor", "co/sensor" -> "co"
            "gas/sensor", "mq5/sensor" -> "gas"
            "AI_fire_alert", "AI_D_fire" -> "ai_fire"
            "water_level/sensor" -> "water"
            "doorbell/sensor" -> "doorbell"
            else -> when {
                topic == "alerts/fire_confirmed" -> "fire_confirmed"
                topic == "alerts/all_true" -> "fire_confirmed"
                topic.startsWith("alerts/fire_confirmed") -> "fire_confirmed"
                topic.startsWith("alerts/all_true") -> "fire_confirmed"
                topic.startsWith("alerts/fire") -> "fire_alert"
                topic.startsWith("alerts/gas") -> "gas"
                topic.startsWith("alerts/co") -> "co"
                topic.startsWith("alerts/water") -> "water"
                topic.startsWith("alerts/doorbell") -> "doorbell"
                else -> "system"
            }
        }
    }

    private fun isNormalPayload(json: JSONObject): Boolean {
        val status = json.optString("status", "").trim().lowercase()
        val event = json.optString("event", "").trim().lowercase()
        val state = json.optString("state", "").trim().lowercase()

        val normalWords = listOf(
            "정상", "normal", "clear", "safe", "off", "false", "ok"
        )

        return normalWords.any { word ->
            status.contains(word) || event.contains(word) || state.contains(word)
        }
    }

    private fun isDetectedPayload(topic: String, json: JSONObject): Boolean {
        val status = json.optString("status", "").trim().lowercase()
        val event = json.optString("event", "").trim().lowercase()
        val type = typeFromTopic(topic)

        val dangerWords = listOf(
            "위험", "감지", "화재",
            "detected", "detect", "alert", "warning", "danger",
            "fire", "gas", "co", "flame", "smoke",
            "pressed", "ring", "bell",
            "water", "leak", "flood", "overflow", "wet"
        )

        if (dangerWords.any { word -> status.contains(word) || event.contains(word) }) {
            return true
        }

        if (json.optBoolean("detected", false)) return true
        if (json.optBoolean("alert", false)) return true
        if (json.optBoolean("pressed", false)) return true
        if (json.optBoolean("ring", false)) return true
        if (json.optBoolean("bell", false)) return true
        if (json.optBoolean("wet", false)) return true
        if (json.optBoolean("overflow", false)) return true

        val intFields = listOf(
            "detected", "alert", "pressed", "ring", "bell", "wet", "overflow"
        )
        if (intFields.any { field -> json.optInt(field, 0) == 1 }) {
            return true
        }

        if (type == "flame" && event.contains("shz_detected")) return true
        if (type == "ai_fire" && event.contains("fire_detected")) return true
        if (type == "water" && event.contains("water")) return true
        if (type == "doorbell" && event.contains("doorbell")) return true

        return false
    }

    private fun parseTextAlertOrNull(topic: String, payload: String): ParsedAlert? {
        val lower = payload.trim().lowercase()

        val normalWords = listOf("0", "false", "off", "normal", "clear", "safe", "정상")
        if (normalWords.any { lower == it || lower.contains(it) }) {
            return null
        }

        val dangerWords = listOf(
            "1", "true", "on", "detected", "alert", "warning", "danger",
            "fire", "flame", "gas", "co", "smoke", "ring", "pressed", "bell",
            "water", "leak", "flood", "overflow", "wet", "감지", "위험", "화재"
        )

        if (!dangerWords.any { lower == it || lower.contains(it) }) {
            return null
        }

        val type = typeFromTopic(topic)

        return ParsedAlert(
            type = type,
            level = defaultLevelForType(type),
            title = titleForType(type),
            body = bodyForType(type),
            timestampMs = System.currentTimeMillis()
        )
    }

    /*
     * raw 센서 메시지 기반 all-True 계산.
     *
     * 여기서는 빨간 긴급 화면 조건만 계산한다.
     * 일반 알림 판단은 parseAlertOrNull()이 따로 수행한다.
     */
    private fun updateFireAllTrueWindow(topic: String, payload: String): Boolean {
        val key = fireSensorKeyFromTopic(topic) ?: fireSensorKeyFromPayload(payload) ?: return false

        val now = System.currentTimeMillis()

        val json = try {
            JSONObject(payload)
        } catch (_: Exception) {
            null
        }

        if (json != null && isNormalPayload(json)) {
            fireDetectedAt[key] = 0L
            allTrueActive = false
            return false
        }

        if (json == null && isNormalText(payload)) {
            fireDetectedAt[key] = 0L
            allTrueActive = false
            return false
        }

        val detected = if (json != null) {
            isDetectedPayload(topic, json)
        } else {
            isDetectedText(payload)
        }

        if (!detected) return false

        /*
         * retained 또는 오래된 timestamp 메시지가 들어와서
         * 앱 실행 직후 엉뚱하게 all-True가 되는 상황을 줄이기 위한 방어.
         * timestamp가 없으면 현재 수신 시각 기준으로 처리한다.
         */
        val payloadTs = json?.let { payloadTimestampMsOrNull(it) }
        if (payloadTs != null && abs(now - payloadTs) > RAW_SENSOR_EVENT_MAX_AGE_MS) {
            return false
        }

        fireDetectedAt[key] = now
        purgeExpiredFireSensorWindow(now)

        val allTrueNow = FIRE_REQUIRED_KEYS.all { requiredKey ->
            val ts = fireDetectedAt[requiredKey] ?: 0L
            ts > 0L && now - ts <= ALL_TRUE_WINDOW_MS
        }

        if (!allTrueNow) {
            allTrueActive = false
            return false
        }

        if (allTrueActive && now - lastEmergencyShownAt < EMERGENCY_REPEAT_BLOCK_MS) {
            return false
        }

        allTrueActive = true
        lastEmergencyShownAt = now
        return true
    }

    private fun purgeExpiredFireSensorWindow(now: Long) {
        FIRE_REQUIRED_KEYS.forEach { key ->
            val ts = fireDetectedAt[key] ?: 0L
            if (ts > 0L && now - ts > ALL_TRUE_WINDOW_MS) {
                fireDetectedAt[key] = 0L
            }
        }

        val allStillValid = FIRE_REQUIRED_KEYS.all { key ->
            val ts = fireDetectedAt[key] ?: 0L
            ts > 0L && now - ts <= ALL_TRUE_WINDOW_MS
        }

        if (!allStillValid) {
            allTrueActive = false
        }
    }

    private fun fireSensorKeyFromTopic(topic: String): String? {
        return when (topic) {
            "shz/sensor", "flame/sensor" -> FIRE_KEY_FLAME
            "mq7/sensor", "co/sensor" -> FIRE_KEY_CO
            "gas/sensor", "mq5/sensor" -> FIRE_KEY_GAS
            "AI_fire_alert", "AI_D_fire" -> FIRE_KEY_AI
            else -> null
        }
    }

    private fun fireSensorKeyFromPayload(payload: String): String? {
        val json = try {
            JSONObject(payload)
        } catch (_: Exception) {
            return null
        }

        val id = json.optString("sensor_id")
            .ifBlank { json.optString("device_id") }
            .ifBlank { json.optString("id") }
            .trim()
            .lowercase()

        val event = json.optString("event", "").trim().lowercase()
        val type = json.optString("type", "").trim().lowercase()

        return when {
            id == "shz_sensor_pico" || event.contains("shz") || type.contains("flame") -> FIRE_KEY_FLAME
            id == "mq7_sensor_pico" || event.contains("mq7") || type == "co" -> FIRE_KEY_CO
            id == "gas_sensor_pico" || id == "mq5_sensor_pico" || event.contains("gas") || event.contains("mq5") -> FIRE_KEY_GAS
            id == "ai_d_fire" || event.contains("fire_detected") || type == "ai_fire" -> FIRE_KEY_AI
            else -> null
        }
    }

    private fun isDetectedText(payload: String): Boolean {
        val lower = payload.trim().lowercase()

        val dangerWords = listOf(
            "1", "true", "on", "detected", "alert", "warning", "danger",
            "fire", "flame", "gas", "co", "smoke", "감지", "위험", "화재"
        )

        return dangerWords.any { lower == it || lower.contains(it) }
    }

    private fun isNormalText(payload: String): Boolean {
        val lower = payload.trim().lowercase()

        val normalWords = listOf(
            "0", "false", "off", "normal", "clear", "safe", "정상"
        )

        return normalWords.any { lower == it || lower.contains(it) }
    }

    /*
     * 판단서버가 명확하게 all-True / fire_confirmed 이벤트를 보내는 경우.
     * 이 경우에는 앱 자체 raw window 계산 없이도 긴급 화면을 띄운다.
     */
    private fun isAllTrueFireAlert(topic: String, payload: String): Boolean {
        if (topic == "alerts/fire_confirmed" || topic == "alerts/all_true") {
            return true
        }

        val lowerText = payload.lowercase()

        val textHit =
            lowerText.contains("all_true") ||
                lowerText.contains("all-true") ||
                lowerText.contains("fire_confirmed") ||
                lowerText.contains("manual_fire_test")

        if (textHit && topic.startsWith("alerts/")) {
            return true
        }

        return try {
            val json = JSONObject(payload)

            val command = json.optString("command", "").lowercase()
            val type = json.optString("type", "").lowercase()
            val event = json.optString("event", "").lowercase()
            val source = json.optString("source", "").lowercase()
            val sensorId = json.optString("sensor_id", "").lowercase()
            val reason = json.optString("reason", "").lowercase()
            val msg = json.optString("msg", "").lowercase()

            command == "fire_confirmed" ||
                command == "all_true" ||
                command == "fire_test" ||
                type == "fire_confirmed" ||
                type == "all_true" ||
                event == "fire_confirmed" ||
                event == "all_true" ||
                source == "all_true" ||
                sensorId == "all_true" ||
                sensorId == "manual_fire_test" ||
                reason.contains("all_true") ||
                msg.contains("all-true") ||
                msg.contains("all_true")
        } catch (_: Exception) {
            false
        }
    }

    private fun isFreshEmergencyPayload(payload: String): Boolean {
        val json = try {
            JSONObject(payload)
        } catch (_: Exception) {
            return true
        }

        val ts = payloadTimestampMsOrNull(json) ?: return true
        val now = System.currentTimeMillis()

        return abs(now - ts) <= SERVER_EMERGENCY_MAX_AGE_MS
    }

    private fun payloadTimestampMsOrNull(json: JSONObject): Long? {
        return when {
            json.has("timestamp_ms") -> json.optLong("timestamp_ms", 0L).takeIf { it > 0L }
            json.has("ts_ms") -> json.optLong("ts_ms", 0L).takeIf { it > 0L }
            else -> null
        }
    }

    private fun showFireConfirmedEmergency(title: String, body: String, timestampMs: Long) {
        val now = System.currentTimeMillis()

        if (now - lastEmergencyShownAt < EMERGENCY_REPEAT_BLOCK_MS) {
            return
        }

        lastEmergencyShownAt = now

        serviceScope.launch {
            AppDatabase.getDatabase(applicationContext)
                .notificationDao()
                .insert(
                    NotificationEntity(
                        title = title,
                        message = body,
                        createdAt = timestampMs
                    )
                )
        }

        showEmergencyNotification(title, body)
    }

    private fun titleForType(type: String): String {
        return when (type) {
            "fire_confirmed" -> FIRE_CONFIRMED_TITLE
            "fire_alert" -> "화재 관련 알림"
            "ai_fire" -> "AI 불 감지"
            "flame" -> "불꽃 감지"
            "gas" -> "가스 감지"
            "co" -> "일산화탄소 감지"
            "water" -> "수위 감지"
            "doorbell" -> "초인종 감지"
            else -> "시스템 알림"
        }
    }

    private fun bodyForType(type: String): String {
        return when (type) {
            "fire_confirmed" -> FIRE_CONFIRMED_BODY
            "fire_alert" -> "화재 관련 신호가 수신되었습니다."
            "ai_fire" -> "AI 카메라에서 불이 감지되었습니다."
            "flame" -> "불꽃 센서가 감지되었습니다."
            "gas" -> "가스 센서가 위험 상태를 감지했습니다."
            "co" -> "일산화탄소 센서가 위험 상태를 감지했습니다."
            "water" -> "수위 센서가 감지되었습니다."
            "doorbell" -> "초인종 버튼이 감지되었습니다."
            else -> "시스템 알림이 수신되었습니다."
        }
    }

    private fun defaultLevelForType(type: String): String {
        return when (type) {
            "fire_confirmed" -> "critical"
            "fire_alert" -> "warning"
            "ai_fire" -> "warning"
            "flame", "gas", "co" -> "warning"
            "water", "doorbell" -> "info"
            else -> "info"
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

    private fun buildServiceNotification() = NotificationCompat.Builder(this, CHANNEL_SERVICE)
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
        if (!canPostNotification()) return

        val intent = Intent(this, EmergencyAlertActivity::class.java).apply {
            putExtra("title", title)
            putExtra("body", body)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }

        val pendingIntent = PendingIntent.getActivity(
            this,
            200,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification = NotificationCompat.Builder(this, CHANNEL_ALERT)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("긴급: $title")
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setContentIntent(pendingIntent)
            .setFullScreenIntent(pendingIntent, true)
            .setAutoCancel(true)
            .build()

        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(System.currentTimeMillis().toInt(), notification)
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

        private const val RECONNECT_DELAY_MS = 10_000L
        private const val FAILURE_NOTIFICATION_THROTTLE_MS = 60_000L

        private const val ALL_TRUE_WINDOW_MS = 15_000L
        private const val RAW_SENSOR_EVENT_MAX_AGE_MS = 20_000L
        private const val SERVER_EMERGENCY_MAX_AGE_MS = 60_000L
        private const val EMERGENCY_REPEAT_BLOCK_MS = 30_000L

        private const val FIRE_KEY_FLAME = "flame"
        private const val FIRE_KEY_CO = "co"
        private const val FIRE_KEY_GAS = "gas"
        private const val FIRE_KEY_AI = "ai_fire"

        private val FIRE_REQUIRED_KEYS = listOf(
            FIRE_KEY_FLAME,
            FIRE_KEY_CO,
            FIRE_KEY_GAS,
            FIRE_KEY_AI
        )

        private const val FIRE_CONFIRMED_TITLE = "화재 감지"
        private const val FIRE_CONFIRMED_BODY =
            "불꽃, 일산화탄소, 가스, AI 불 감지가 15초 안에 모두 감지되어 화재 위험으로 판단되었습니다."

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
