package com.example.interfaceui.util

import android.content.Context
import android.graphics.Bitmap
import androidx.appcompat.content.res.AppCompatResources
import androidx.core.graphics.drawable.toBitmap
import com.example.interfaceui.R

object AlertNotificationVisuals {

    fun resolveType(explicitType: String?, title: String?, body: String?): String {
        val normalized = normalizeExplicitType(explicitType)
        if (normalized != null) return normalized

        val text = "${title.orEmpty()} ${body.orEmpty()}".trim().lowercase()

        return when {
            text.contains("화재 확정") || text.contains("fire_confirmed") || text.contains("all_true") -> "fire_confirmed"
            text.contains("ai 불") || (text.contains("ai") && text.contains("카메라") && text.contains("불")) -> "ai_fire"
            text.contains("불꽃") || text.contains("flame") -> "flame"
            text.contains("일산화탄소") || text.contains(" co ") || text.contains("mq7") -> "co"
            text.contains("가스") || text.contains("gas") || text.contains("mq5") -> "gas"
            text.contains("수위") || text.contains("누수") || text.contains("물 넘침") || text.contains("water") -> "water"
            text.contains("초인종") || text.contains("button_pressed") || text.contains("doorbell") || text.contains("문") -> "doorbell"
            text.contains("화재") || text.contains("불") -> "fire_alert"
            else -> "system"
        }
    }

    private fun normalizeExplicitType(type: String?): String? {
        val t = type?.trim()?.lowercase() ?: return null
        return when (t) {
            "fire_confirmed", "all_true" -> "fire_confirmed"
            "fire_alert", "fire" -> "fire_alert"
            "ai_fire", "ai_d_fire" -> "ai_fire"
            "flame", "shz", "shz_detected" -> "flame"
            "gas", "mq5" -> "gas"
            "co", "mq7" -> "co"
            "water", "water_level" -> "water"
            "doorbell", "button_pressed" -> "doorbell"
            else -> null
        }
    }

    fun largeIconBitmap(context: Context, type: String): Bitmap? {
        val resId = largeIconRes(type)
        val drawable = AppCompatResources.getDrawable(context, resId) ?: return null
        return drawable.toBitmap(128, 128)
    }

    fun largeIconRes(type: String): Int {
        return when (type) {
            "fire_confirmed", "fire_alert" -> R.drawable.ic_alert_fire
            "ai_fire" -> R.drawable.ic_alert_ai_fire
            "flame" -> R.drawable.ic_alert_flame
            "gas" -> R.drawable.ic_alert_gas
            "co" -> R.drawable.ic_alert_co
            "water" -> R.drawable.ic_alert_water
            "doorbell" -> R.drawable.ic_alert_doorbell
            else -> R.drawable.ic_notification
        }
    }

    // 상태바 small icon은 너무 복잡하면 깨질 수 있어서
    // 우선은 공통 아이콘 유지하는 방식을 추천
    fun smallIconRes(): Int = R.drawable.ic_notification
}
