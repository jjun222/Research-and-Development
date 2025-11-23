package com.example.interfaceui.data

import android.content.Context
import info.mqtt.android.service.MqttAndroidClient   // ← 여기!
import org.eclipse.paho.client.mqttv3.IMqttActionListener
import org.eclipse.paho.client.mqttv3.IMqttDeliveryToken
import org.eclipse.paho.client.mqttv3.IMqttToken
import org.eclipse.paho.client.mqttv3.MqttCallbackExtended
import org.eclipse.paho.client.mqttv3.MqttConnectOptions
import org.eclipse.paho.client.mqttv3.MqttMessage
import org.json.JSONObject
import java.nio.charset.Charset
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Go Log 화면에 들어가지 않아도 항상 interfaceui/logs/# 를 구독해
 * LogStore에 누적 저장하는 상시 수집기.
 */
object LogCapture {

    private const val SERVER_URI = "tcp://192.168.0.24:1883"
    private const val TOPIC_LOGS = "interfaceui/logs/#"

    private val started = AtomicBoolean(false)
    private var client: MqttAndroidClient? = null

    fun start(context: Context) {
        if (started.getAndSet(true)) return
        val app = context.applicationContext

        val cid = "logcap-" + System.currentTimeMillis()
        val c = MqttAndroidClient(app, SERVER_URI, cid)
        client = c

        c.setCallback(object : MqttCallbackExtended {
            override fun connectComplete(reconnect: Boolean, serverURI: String?) {
                try {
                    c.subscribe(TOPIC_LOGS, 0, null, object : IMqttActionListener {
                        override fun onSuccess(asyncActionToken: IMqttToken?) {}
                        override fun onFailure(asyncActionToken: IMqttToken?, exception: Throwable?) {}
                    })
                } catch (_: Exception) {}
            }
            override fun connectionLost(cause: Throwable?) { /* auto-reconnect */ }

            override fun messageArrived(topic: String?, message: MqttMessage?) {
                val t = topic ?: return
                if (!t.startsWith("interfaceui/logs/") ||
                    t.startsWith("interfaceui/logs/history/")) return

                val payload = try {
                    message?.payload?.toString(Charset.forName("UTF-8")) ?: ""
                } catch (_: Exception) { "" }

                handleLog(t, payload)
            }
            override fun deliveryComplete(token: IMqttDeliveryToken?) {}
        })

        val opts = MqttConnectOptions().apply {
            isAutomaticReconnect = true
            isCleanSession = false
            keepAliveInterval = 30
        }
        try {
            c.connect(opts, null, object : IMqttActionListener {
                override fun onSuccess(asyncActionToken: IMqttToken?) { /* subscribe는 connectComplete에서 */ }
                override fun onFailure(asyncActionToken: IMqttToken?, exception: Throwable?) { /* auto-reconnect가 재시도 */ }
            })
        } catch (_: Exception) { }
    }

    private fun handleLog(topic: String, raw: String) {
        try {
            val p = topic.split('/')
            val typ = p.getOrNull(2) ?: return
            val id  = p.getOrNull(3) ?: return

            val j = try { JSONObject(raw) } catch (_: Exception) { JSONObject() }
            val type  = j.optString("type", typ)
            val devId = j.optString("id",   id)
            val level = j.optString("level","info")
            val msg   = j.optString("msg",  if (raw.isNotEmpty()) raw.take(200) else "")
            val ts    = j.optLong("ts", System.currentTimeMillis()/1000)

            LogStore.insertAsync(LogStore.Rec(type = type, id = devId, level = level, msg = msg, ts = ts))
        } catch (_: Exception) { /* ignore */ }
    }
}
