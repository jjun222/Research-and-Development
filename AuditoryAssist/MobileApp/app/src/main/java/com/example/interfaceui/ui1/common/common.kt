package com.example.interfaceui.ui1.common

import androidx.annotation.IdRes
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.appbar.MaterialToolbar

/**
 * 레이아웃의 MaterialToolbar를 액션바로 올리고
 * Up(뒤로가기) 버튼과 제목을 설정합니다.
 * 레이아웃에는 @+id/toolbar 가 있어야 합니다.
 */
fun AppCompatActivity.setupUpToolbar(
    @IdRes toolbarId: Int,
    title: CharSequence? = null
) {
    val tb = findViewById<MaterialToolbar>(toolbarId)
    setSupportActionBar(tb)
    supportActionBar?.setDisplayHomeAsUpEnabled(true)
    if (title != null) supportActionBar?.title = title
    tb.setNavigationOnClickListener { onBackPressedDispatcher.onBackPressed() }
}
