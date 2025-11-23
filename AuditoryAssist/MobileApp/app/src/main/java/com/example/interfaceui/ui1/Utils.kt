package com.example.interfaceui.ui1

import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.appbar.MaterialToolbar

/** 모든 화면에서 동일한 Up(뒤로가기) 버튼을 간단히 세팅 */
fun AppCompatActivity.setupUpToolbar(
    toolbarId: Int,
    title: CharSequence? = null
) {
    val tb = findViewById<MaterialToolbar>(toolbarId)
    setSupportActionBar(tb)
    supportActionBar?.setDisplayHomeAsUpEnabled(true)   // ← 좌측 화살표 자동 표시
    if (title != null) supportActionBar?.title = title
    tb.setNavigationOnClickListener { onBackPressedDispatcher.onBackPressed() }
}
