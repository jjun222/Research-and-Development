package com.example.interfaceui.broker

import android.content.Context

object BrokerPrefs {
    private const val PREF_NAME = "broker_prefs"

    private const val KEY_URI = "last_broker_uri"
    private const val KEY_VIDEO_URL = "last_video_url"
    private const val KEY_SNAPSHOT_URL = "last_snapshot_url"
    private const val KEY_DISPLAY_NAME = "last_broker_name"

    fun getBrokerUriOrNull(context: Context): String? {
        val prefs = context.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
        return prefs.getString(KEY_URI, null)
            ?.trim()
            ?.takeIf { isValidMqttUri(it) }
    }

    fun requireBrokerUri(context: Context): String {
        return getBrokerUriOrNull(context)
            ?: throw IllegalStateException(
                "저장된 MQTT Broker URI가 없습니다. 먼저 브로커 검색 또는 수동 등록이 필요합니다."
            )
    }

    fun saveBrokerUri(context: Context, uri: String) {
        require(isValidMqttUri(uri)) { "MQTT URI는 tcp://host:port 형식이어야 합니다. uri=$uri" }

        context.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_URI, uri.trim())
            .apply()
    }

    fun saveBrokerInfo(
        context: Context,
        uri: String,
        videoUrl: String? = null,
        snapshotUrl: String? = null,
        displayName: String? = null
    ) {
        require(isValidMqttUri(uri)) { "MQTT URI는 tcp://host:port 형식이어야 합니다. uri=$uri" }

        val editor = context.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_URI, uri.trim())

        if (!videoUrl.isNullOrBlank() && isHttpUrl(videoUrl)) {
            editor.putString(KEY_VIDEO_URL, videoUrl.trim())
        }

        if (!snapshotUrl.isNullOrBlank() && isHttpUrl(snapshotUrl)) {
            editor.putString(KEY_SNAPSHOT_URL, snapshotUrl.trim())
        }

        if (!displayName.isNullOrBlank()) {
            editor.putString(KEY_DISPLAY_NAME, displayName.trim())
        }

        editor.apply()
    }

    fun saveCameraInfo(
        context: Context,
        videoUrl: String,
        snapshotUrl: String? = null,
        displayName: String? = null
    ) {
        require(isHttpUrl(videoUrl)) { "videoUrl은 http:// 또는 https:// 형식이어야 합니다. videoUrl=$videoUrl" }

        val editor = context.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_VIDEO_URL, videoUrl.trim())

        if (!snapshotUrl.isNullOrBlank() && isHttpUrl(snapshotUrl)) {
            editor.putString(KEY_SNAPSHOT_URL, snapshotUrl.trim())
        }

        if (!displayName.isNullOrBlank()) {
            editor.putString(KEY_DISPLAY_NAME, displayName.trim())
        }

        editor.apply()
    }

    fun getVideoUrlOrNull(context: Context): String? {
        val prefs = context.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
        return prefs.getString(KEY_VIDEO_URL, null)
            ?.trim()
            ?.takeIf { isHttpUrl(it) }
    }

    fun getSnapshotUrlOrNull(context: Context): String? {
        val prefs = context.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
        return prefs.getString(KEY_SNAPSHOT_URL, null)
            ?.trim()
            ?.takeIf { isHttpUrl(it) }
    }

    fun getDisplayNameOrNull(context: Context): String? {
        val prefs = context.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
        return prefs.getString(KEY_DISPLAY_NAME, null)
            ?.trim()
            ?.takeIf { it.isNotBlank() }
    }

    fun clear(context: Context) {
        context.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .clear()
            .apply()
    }

    fun clearCameraInfo(context: Context) {
        context.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_VIDEO_URL)
            .remove(KEY_SNAPSHOT_URL)
            .apply()
    }

    private fun isValidMqttUri(uri: String): Boolean {
        val v = uri.trim()
        if (!v.startsWith("tcp://")) return false

        val body = v.removePrefix("tcp://")
        val parts = body.split(":")
        if (parts.size != 2) return false
        if (parts[0].isBlank()) return false

        val port = parts[1].toIntOrNull() ?: return false
        return port in 1..65535
    }

    private fun isHttpUrl(url: String): Boolean {
        val v = url.trim()
        return v.startsWith("http://") || v.startsWith("https://")
    }
}
