package com.example.interfaceui.alias

import android.content.Context
import android.content.SharedPreferences
import java.util.Locale

object DeviceAlias {

    private const val PREF = "device_alias"

    private val defaults = mapOf(
        "server" to "중앙 관리 서버",

        "Neopixel_1" to "거실 무드등",
        "Neopixel_2" to "안방 무드등",

        "shz_sensor_pico" to "주방 불꽃 센서",
        "gas_sensor_pico" to "주방 가스 센서",
        "mq7_sensor_pico" to "거실 일산화탄소 센서",
        "AI_D_fire" to "AI 화재 카메라",

        "water_level_1" to "욕실 수위 센서",
        "doorbell_1" to "현관 초인종",

        "Vibrator_1" to "침대 진동 알림",
        "Beacon_1" to "경광등"
    )

    private val normalizers = mapOf(
        "server" to "server",

        "neopixel_1" to "Neopixel_1",
        "neopixel_2" to "Neopixel_2",

        "shz_sensor_pico" to "shz_sensor_pico",
        "gas_sensor_pico" to "gas_sensor_pico",
        "mq7_sensor_pico" to "mq7_sensor_pico",
        "ai_d_fire" to "AI_D_fire",

        "water_level_1" to "water_level_1",
        "waterlevel_1" to "water_level_1",
        "doorbell_1" to "doorbell_1",

        "vibrator_1" to "Vibrator_1",
        "beacon_1" to "Beacon_1"
    )

    fun canonicalId(raw: String?): String {
        val s = raw?.trim().orEmpty()
        val fixed = s.lowercase(Locale.getDefault()).replace('-', '_')
        return normalizers[fixed] ?: s
    }

    fun allowedIds(): Set<String> = setOf(
        "server",
        "Neopixel_1",
        "Neopixel_2",
        "shz_sensor_pico",
        "gas_sensor_pico",
        "mq7_sensor_pico",
        "AI_D_fire",
        "water_level_1",
        "doorbell_1",
        "Vibrator_1",
        "Beacon_1"
    )

    fun shouldShow(id: String): Boolean = allowedIds().contains(canonicalId(id))

    fun labelFor(id: String): String {
        val cid = canonicalId(id)
        return defaults[cid] ?: cid
    }

    private fun prefs(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE)

    fun resolve(ctx: Context, id: String, originalName: String?): String {
        val cid = canonicalId(id)
        val user = prefs(ctx).getString(cid, null)
        return user ?: defaults[cid] ?: originalName ?: cid
    }

    fun set(ctx: Context, id: String, alias: String?) {
        val cid = canonicalId(id)
        prefs(ctx).edit().apply {
            if (alias.isNullOrBlank()) remove(cid) else putString(cid, alias)
        }.apply()
    }
}
