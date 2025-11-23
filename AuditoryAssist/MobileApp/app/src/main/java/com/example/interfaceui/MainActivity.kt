package com.example.interfaceui

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.NotificationManagerCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.example.interfaceui.service.PushAlertService
import com.example.interfaceui.ui1.CheckActivity
import com.example.interfaceui.ui1.DeviceSelectActivity
import com.example.interfaceui.ui1.LiveVideoActivity
import com.example.interfaceui.ui1.LogActivity
import com.example.interfaceui.ui1.NotificationActivity
import com.example.interfaceui.ui1.SettingActivity
import com.example.interfaceui.ui1.SituationStatusActivity

class MainActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_main)

        // 상태바와 겹치지 않게 top padding 적용
        val root = findViewById<ViewGroup>(R.id.root)
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            val sysBars = insets.getInsets(WindowInsetsCompat.Type.statusBars())
            v.updatePadding(top = sysBars.top)
            insets
        }

        // ✅ 상황 상태 확인
        findViewById<View>(R.id.btnMoveSituationStatus).setOnClickListener {
            startActivity(Intent(this, SituationStatusActivity::class.java))
        }

        // 버튼 클릭 -> 각 화면 이동
        findViewById<View>(R.id.btnMoveCheck).setOnClickListener {
            startActivity(Intent(this, CheckActivity::class.java))
        }
        findViewById<View>(R.id.btnMoveLog).setOnClickListener {
            startActivity(Intent(this, LogActivity::class.java))
        }
        findViewById<View>(R.id.btnMoveSetting).setOnClickListener {
            startActivity(Intent(this, SettingActivity::class.java))
        }
        // 🔴 여기서 바로 LiveVideoActivity로 진입
        findViewById<View>(R.id.btnMoveCamera).setOnClickListener {
            startActivity(Intent(this, LiveVideoActivity::class.java))
        }
        findViewById<View>(R.id.btnMoveDevice).setOnClickListener {
            startActivity(Intent(this, DeviceSelectActivity::class.java))
        }
        findViewById<View>(R.id.btnMoveNotification).setOnClickListener {
            startActivity(Intent(this, NotificationActivity::class.java))
        }

        // 🔔 알림 권한 요청 (Android 13+)
        requestPostNotificationIfNeeded()

        // ✅ FCM 토큰을 MQTT로 등록
        PushAlertService.ensureTokenRegistered(applicationContext)
    }

    private fun requestPostNotificationIfNeeded() {
        if (Build.VERSION.SDK_INT >= 33) {
            val enabled = NotificationManagerCompat.from(this).areNotificationsEnabled()
            if (!enabled) {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
            }
        }
    }
}
