package com.example.carecall_app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import android.os.Bundle
import io.flutter.embedding.android.FlutterActivity

class MainActivity : FlutterActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        createCareCallNotificationChannel()
    }

    private fun createCareCallNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return
        }

        val notificationManager =
            getSystemService(NotificationManager::class.java) ?: return

        val channel = NotificationChannel(
            "carecall_alerts",
            "CareCall 안전 알림",
            NotificationManager.IMPORTANCE_DEFAULT
        )
        channel.description = "낙상 의심 및 보호자 확인이 필요한 CareCall 알림"

        notificationManager.createNotificationChannel(channel)
    }
}
