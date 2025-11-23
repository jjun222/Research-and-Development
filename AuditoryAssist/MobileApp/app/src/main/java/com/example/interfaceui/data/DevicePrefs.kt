package com.example.interfaceui.data

import android.content.Context
import org.json.JSONArray
import org.json.JSONException
import java.util.LinkedHashSet

object DevicePrefs {
    private const val PREF = "device_prefs_v2"

    // 목록(JSON 배열)과 선택값
    private const val KEY_DEV_LIST = "device_list"
    private const val KEY_SELECTED = "selected_device" // null 이면 ALL

    // --- 리스트 ---

    fun getDevices(ctx: Context): List<String> {
        val sp = ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE)
        val raw = sp.getString(KEY_DEV_LIST, "[]") ?: "[]"
        return try {
            val ja = JSONArray(raw)
            // buildList 사용 시 람다에는 인덱스 매개변수가 없음
            buildList(ja.length()) {
                for (i in 0 until ja.length()) {
                    add(ja.optString(i))
                }
            }
        } catch (_: JSONException) {
            emptyList()
        }
    }

    fun addDevice(ctx: Context, id: String) {
        if (id.isBlank()) return
        val cur = LinkedHashSet(getDevices(ctx))
        cur += id.trim()
        saveList(ctx, cur.toList())
    }

    fun removeDevice(ctx: Context, id: String) {
        if (id.isBlank()) return
        val cur = LinkedHashSet(getDevices(ctx))
        cur -= id.trim()
        saveList(ctx, cur.toList())
        if (getSelected(ctx) == id) setSelected(ctx, null) // 선택값이 삭제되면 ALL 처리
    }

    private fun saveList(ctx: Context, list: List<String>) {
        val ja = JSONArray()
        list.forEach { ja.put(it) }
        ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_DEV_LIST, ja.toString())
            .apply()
    }

    // --- 선택값 ---

    /** 선택한 단일 기기ID (없으면 null=ALL) */
    fun getSelected(ctx: Context): String? =
        ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).getString(KEY_SELECTED, null)

    /** 선택값 저장 (null이면 ALL 의미) */
    fun setSelected(ctx: Context, id: String?) {
        ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_SELECTED, id)
            .apply()
    }
}
