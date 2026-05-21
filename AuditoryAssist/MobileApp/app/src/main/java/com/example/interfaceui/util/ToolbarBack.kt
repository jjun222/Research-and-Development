package com.example.interfaceui.util

import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import com.example.interfaceui.R
import com.google.android.material.appbar.MaterialToolbar

fun AppCompatActivity.setupToolbarBack(
    toolbarId: Int = R.id.toolbar,
    titleText: CharSequence? = null
) {
    val toolbarView = findViewById<View?>(toolbarId) ?: return

    when (toolbarView) {
        is MaterialToolbar -> {
            titleText?.let { toolbarView.title = it }
            toolbarView.setNavigationIcon(R.drawable.ic_arrow_back_24)
            toolbarView.setNavigationOnClickListener {
                finish()
            }
        }

        is Toolbar -> {
            titleText?.let { toolbarView.title = it }
            toolbarView.setNavigationIcon(R.drawable.ic_arrow_back_24)
            toolbarView.setNavigationOnClickListener {
                finish()
            }
        }
    }
}
