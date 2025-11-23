// PushAlertService.kt
package com.example.interfaceui.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.example.interfaceui.MainActivity
import com.example.interfaceui.MqttHelper
import com.example.interfaceui.R
import com.example.interfaceui.data.AppDatabase
import com.example.interfaceui.data.NotificationEntity
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

class PushAlertService : FirebaseMessagingService() {

    private val serviceJob = SupervisorJob()
    private val ioScope = CoroutineScope(serviceJob + Dispatchers.IO)

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onDestroy() {
        super.onDestroy()
        ioScope.cancel()
    }

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        Log.d(TAG, "onNewToken: $token")
        publish(applicationContext, token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        super.onMessageReceived(message)
        Log.d(TAG, "message: ${message.data} / ${message.notification}")

        val title = message.notification?.title ?: message.data["title"] ?: "알림"
        val body  = message.notification?.body  ?: message.data["body"]  ?: "내용 없음"

        showLocalNotification(title, body)

        // ★ Room DB에 저장 → [푸시 알림] 화면에서 목록으로 보여줌
        ioScope.launch {
            runCatching {
                val dao = AppDatabase.getDatabase(applicationContext).notificationDao()
                dao.insert(NotificationEntity(title = title, message = body))
            }.onFailure { e ->
                Log.e(TAG, "save notification to DB failed", e)
            }
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(
                CHANNEL_ID,
                "알림(긴급)",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "센서/화재 관련 긴급 알림"
                enableVibration(true)
            }
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.createNotificationChannel(ch)
        }
    }

    private fun showLocalNotification(title: String, body: String) {
        // Android 13+ 알림 권한 체크
        if (Build.VERSION.SDK_INT >= 33) {
            val granted = ContextCompat.checkSelfPermission(
                this, android.Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                Log.w(TAG, "알림 권한 없음 → 로컬 알림 생략")
                return
            }
        }

        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val piFlags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M)
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        else
            PendingIntent.FLAG_UPDATE_CURRENT
        val pendingIntent = PendingIntent.getActivity(this, 0, intent, piFlags)

        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_alert) // 필요 시 앱 아이콘으로 교체 가능
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(pendingIntent)

        try {
            NotificationManagerCompat.from(this)
                .notify((System.currentTimeMillis() % 100_000_000).toInt(), builder.build())
        } catch (se: SecurityException) {
            Log.e(TAG, "notify() SecurityException", se)
        }
    }

    companion object {
        private const val TAG = "PushAlertService"
        private const val TOPIC_REGISTER = "interfaceui/push/register"
        private const val CHANNEL_ID = "alerts"

        /** 앱 시작 시 한 번 호출해서 토큰을 MQTT로 등록 */
        fun ensureTokenRegistered(appContext: Context) {
            FirebaseMessaging.getInstance().token
                .addOnSuccessListener { token ->
                    Log.d(TAG, "fetched token: $token")
                    publish(appContext, token)
                }
                .addOnFailureListener { e ->
                    Log.e(TAG, "get token failed", e)
                }
        }

        /** MQTT로 토큰 전송 */
        private fun publish(appContext: Context, token: String) {
            val payload = """{"token":"$token"}"""
            MqttHelper.connect(
                context = appContext,
                onConnected = { ok ->
                    if (ok) {
                        MqttHelper.instance?.publish(
                            TOPIC_REGISTER, payload, qos = 1, retain = false
                        )
                        Log.d(TAG, "token published via MQTT")
                    } else {
                        Log.e(TAG, "MQTT connect failed for token publish")
                    }
                }
            )
        }
    }
}
