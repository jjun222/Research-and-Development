package com.example.interfaceui.ui1

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

/**
 * 기존 "카메라 모드" 메뉴가 이 액티비티를 열고 있으므로
 * 여기서 즉시 LiveVideoActivity로 포워딩합니다.
 */
class CameraActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startActivity(Intent(this, LiveVideoActivity::class.java))
        finish()
    }
}
