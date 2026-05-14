package com.example.interfaceui.broker

import android.content.Context

object BrokerPrefs {

    private const val PREF_NAME = "broker_prefs"

    private const val KEY_URI = "last_broker_uri"
    private const val KEY_VIDEO_URL = "last_video_url"
    private const val KEY_DISPLAY_NAME = "last_broker_name"

    fun getBrokerUriOrNull(context: Context): String? {
        val prefs = context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
        return prefs.getString(KEY_URI, null)
            ?.takeIf { it.startsWith("tcp://") && it.contains(":") }
    }

    fun requireBrokerUri(context: Context): String {
        return getBrokerUriOrNull(context)
            ?: throw IllegalStateException("저장된 MQTT Broker URI가 없습니다. 먼저 브로커 검색 또는 수동 등록이 필요합니다.")
    }

    fun saveBrokerUri(context: Context, uri: String) {
        require(uri.startsWith("tcp://")) {
            "MQTT URI는 tcp:// 형식이어야 합니다. uri=$uri"
        }

        context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_URI, uri)
            .apply()
    }

    fun saveBrokerInfo(
        context: Context,
        uri: String,
        videoUrl: String? = null,
        displayName: String? = null
    ) {
        require(uri.startsWith("tcp://")) {
            "MQTT URI는 tcp:// 형식이어야 합니다. uri=$uri"
        }

        context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_URI, uri)
            .apply {
                if (!videoUrl.isNullOrBlank()) putString(KEY_VIDEO_URL, videoUrl)
                if (!displayName.isNullOrBlank()) putString(KEY_DISPLAY_NAME, displayName)
            }
            .apply()
    }

    fun getVideoUrlOrNull(context: Context): String? {
        val prefs = context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
        return prefs.getString(KEY_VIDEO_URL, null)
    }

    fun getDisplayNameOrNull(context: Context): String? {
        val prefs = context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
        return prefs.getString(KEY_DISPLAY_NAME, null)
    }

    fun clear(context: Context) {
        context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .clear()
            .apply()
    }
}
