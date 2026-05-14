package com.example.interfaceui.service

import android.content.Context
import android.util.Log
import com.example.interfaceui.MqttHelper
import org.json.JSONObject

object PushTokenRegistrar {

    private const val TAG = "PushTokenRegistrar"
    private const val PREF_NAME = "push_token_prefs"
    private const val KEY_PENDING_TOKEN = "pending_fcm_token"

    private const val TOPIC_PUSH_REGISTER = "interfaceui/push/register"

    fun savePendingToken(context: Context, token: String) {
        context.applicationContext
            .getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_PENDING_TOKEN, token)
            .apply()
    }

    fun getPendingToken(context: Context): String? {
        return context.applicationContext
            .getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .getString(KEY_PENDING_TOKEN, null)
            ?.takeIf { it.isNotBlank() }
    }

    fun clearPendingToken(context: Context) {
        context.applicationContext
            .getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_PENDING_TOKEN)
            .apply()
    }

    fun flushPendingToken(context: Context): Boolean {
        val appContext = context.applicationContext
        val token = getPendingToken(appContext) ?: return true
        val helper = MqttHelper.instance ?: return false

        val payload = JSONObject()
            .put("token", token)
            .put("platform", "android")
            .put("ts_ms", System.currentTimeMillis())
            .toString()

        val ok = helper.publish(
            topic = TOPIC_PUSH_REGISTER,
            payload = payload,
            qos = 1,
            retain = false
        )

        if (ok) {
            clearPendingToken(appContext)
            Log.d(TAG, "FCM token registered by MQTT")
        } else {
            Log.w(TAG, "FCM token registration pending: MQTT not connected")
        }

        return ok
    }
}
