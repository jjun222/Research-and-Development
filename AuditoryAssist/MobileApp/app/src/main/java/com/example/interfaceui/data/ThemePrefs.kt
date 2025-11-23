package com.example.interfaceui.data

import android.content.Context
import android.content.res.ColorStateList
import android.view.View
import androidx.annotation.ColorInt
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView

object ThemePrefs {
    private const val PREF = "ui_prefs"
    private const val KEY  = "accent_color"

    @ColorInt
    fun load(context: Context, @ColorInt defColor: Int): Int =
        context.getSharedPreferences(PREF, Context.MODE_PRIVATE)
            .getInt(KEY, defColor)

    fun save(context: Context, @ColorInt color: Int) {
        context.getSharedPreferences(PREF, Context.MODE_PRIVATE)
            .edit().putInt(KEY, color).apply()
    }
}

fun applyAccent(@ColorInt color: Int, vararg targets: View) {
    targets.forEach { v ->
        when (v) {
            is MaterialToolbar -> v.setBackgroundColor(color)
            is MaterialButton  -> v.backgroundTintList = ColorStateList.valueOf(color)
            is MaterialCardView -> v.setCardBackgroundColor(color)
            else -> v.setBackgroundColor(color)
        }
    }
}
