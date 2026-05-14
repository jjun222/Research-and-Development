package com.example.interfaceui.ui1

import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.example.interfaceui.R

class EmergencyAlertActivity : AppCompatActivity() {

    private var vibrator: Vibrator? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_emergency_alert)

        val title = intent.getStringExtra("title") ?: "긴급 알림"
        val body = intent.getStringExtra("body") ?: "위험 상황이 감지되었습니다."

        findViewById<TextView>(R.id.tvEmergencyTitle).text = title
        findViewById<TextView>(R.id.tvEmergencyBody).text = body

        findViewById<android.view.View>(R.id.btnEmergencyConfirm).setOnClickListener {
            finish()
        }

        startVibration()
    }

    override fun onDestroy() {
        vibrator?.cancel()
        super.onDestroy()
    }

    private fun startVibration() {
        vibrator = if (android.os.Build.VERSION.SDK_INT >= 31) {
            val manager = getSystemService(VibratorManager::class.java)
            manager.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            getSystemService(VIBRATOR_SERVICE) as Vibrator
        }

        val pattern = longArrayOf(0, 500, 200, 500, 200, 800)

        if (android.os.Build.VERSION.SDK_INT >= 26) {
            vibrator?.vibrate(
                VibrationEffect.createWaveform(pattern, 0)
            )
        } else {
            @Suppress("DEPRECATION")
            vibrator?.vibrate(pattern, 0)
        }
    }
}
