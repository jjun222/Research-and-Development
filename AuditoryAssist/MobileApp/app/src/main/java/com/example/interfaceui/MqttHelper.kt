package com.example.interfaceui

import android.content.Context
import android.util.Log
import info.mqtt.android.service.MqttAndroidClient
import org.eclipse.paho.client.mqttv3.*
import java.util.concurrent.CopyOnWriteArraySet

class MqttHelper private constructor(
    private val app: Context,
    private val serverUri: String = DEFAULT_URI,
    clientId: String = "android-" + System.currentTimeMillis()
) {
    private val client = MqttAndroidClient(app, serverUri, clientId)

    // 레거시 단일 리스너
    var messageListener: ((topic: String, message: String) -> Unit)? = null

    // 멀티 리스너
    private val listeners = CopyOnWriteArraySet<(String, String) -> Unit>()

    // 재구독용 저장
    private val subscribedTopics = CopyOnWriteArraySet<Pair<String, Int>>()

    val currentServerUri: String
        get() = serverUri

    internal fun snapshotListeners(): List<(String, String) -> Unit> = listeners.toList()
    internal fun snapshotSubscriptions(): Set<Pair<String, Int>> = subscribedTopics.toSet()

    private val options = MqttConnectOptions().apply {
        isAutomaticReconnect = true
        isCleanSession = false
        connectionTimeout = 10
        keepAliveInterval = 30
        setWill("clients/$clientId/lwt", "offline".toByteArray(), 1, true)
    }

    @Volatile
    private var connecting = false

    init {
        client.setCallback(object : MqttCallbackExtended {
            override fun connectComplete(reconnect: Boolean, serverURI: String?) {
                Log.d(TAG, "MQTT connectComplete (reconnect=$reconnect, uri=$serverURI)")

                // 초기 연결 / 재연결 / 브로커 전환 후 구독 복구
                subscribedTopics.forEach { (topic, qos) ->
                    try {
                        client.subscribe(topic, qos, null, null)
                        Log.d(TAG, "subscribed on connectComplete: $topic")
                    } catch (e: Exception) {
                        Log.w(TAG, "subscribe restore failed: $topic", e)
                    }
                }
            }

            override fun connectionLost(cause: Throwable?) {
                Log.w(TAG, "MQTT connectionLost", cause)
            }

            override fun messageArrived(topic: String?, message: MqttMessage?) {
                val t = topic ?: return
                val m = message?.toString() ?: ""

                for (cb in listeners) {
                    runCatching { cb(t, m) }
                }

                messageListener?.invoke(t, m)
                Log.d(TAG, "MQTT messageArrived topic=$t payload=$m")
            }

            override fun deliveryComplete(token: IMqttDeliveryToken?) = Unit
        })
    }

    fun addMessageListener(callback: (String, String) -> Unit) {
        listeners.add(callback)
    }

    fun removeMessageListener(callback: (String, String) -> Unit) {
        listeners.remove(callback)
    }

    fun connect(
        onConnected: (() -> Unit)? = null,
        onError: ((Throwable) -> Unit)? = null
    ) {
        if (client.isConnected) {
            onConnected?.invoke()
            return
        }
        if (connecting) return

        connecting = true
        try {
            client.connect(options, null, object : IMqttActionListener {
                override fun onSuccess(asyncActionToken: IMqttToken?) {
                    connecting = false
                    Log.d(TAG, "MQTT connected")
                    onConnected?.invoke()
                }

                override fun onFailure(asyncActionToken: IMqttToken?, exception: Throwable?) {
                    connecting = false
                    Log.e(TAG, "MQTT connect failed", exception)
                    onError?.invoke(exception ?: Exception("connect failed"))
                }
            })
        } catch (e: MqttException) {
            connecting = false
            onError?.invoke(e)
        }
    }

    fun publish(
        topic: String,
        payload: String,
        qos: Int = 1,
        retain: Boolean = false
    ): Boolean {
        if (!client.isConnected) return false

        return try {
            val msg = MqttMessage(payload.toByteArray(Charsets.UTF_8)).apply {
                this.qos = qos
                isRetained = retain
            }
            client.publish(topic, msg)
            true
        } catch (e: MqttException) {
            Log.e(TAG, "publish failed: $topic", e)
            false
        }
    }

    fun subscribe(topic: String, qos: Int = 1) {
        subscribedTopics.add(topic to qos)

        if (!client.isConnected) return

        try {
            client.subscribe(topic, qos, null, object : IMqttActionListener {
                override fun onSuccess(asyncActionToken: IMqttToken?) {
                    Log.d(TAG, "subscribed: $topic")
                }

                override fun onFailure(asyncActionToken: IMqttToken?, exception: Throwable?) {
                    Log.e(TAG, "subscribe failed: $topic", exception)
                }
            })
        } catch (e: MqttException) {
            Log.e(TAG, "subscribe exception", e)
        }
    }

    fun disconnect() {
        connecting = false
        try {
            if (client.isConnected) client.disconnect()
        } catch (_: Exception) {
        }
    }

    companion object {
        private const val TAG = "MqttHelper"
        private const val DEFAULT_URI = "tcp://192.168.0.33:1883"

        @Volatile
        private var _instance: MqttHelper? = null

        val instance: MqttHelper?
            get() = _instance

        fun init(
            context: Context,
            serverUri: String = DEFAULT_URI,
            clientId: String = "android-" + System.currentTimeMillis()
        ): MqttHelper {
            if (_instance == null) {
                synchronized(this) {
                    if (_instance == null) {
                        _instance = MqttHelper(context.applicationContext, serverUri, clientId)
                    }
                }
            }
            return _instance!!
        }

        fun switchServer(
            context: Context,
            serverUri: String,
            clientId: String = "android-" + System.currentTimeMillis()
        ): MqttHelper {
            synchronized(this) {
                val old = _instance

                if (old != null && old.currentServerUri == serverUri) {
                    return old
                }

                val oldListeners = old?.snapshotListeners().orEmpty()
                val oldMessageListener = old?.messageListener
                val oldSubscriptions = old?.snapshotSubscriptions().orEmpty()

                old?.disconnect()

                val next = MqttHelper(context.applicationContext, serverUri, clientId)
                next.messageListener = oldMessageListener

                oldListeners.forEach { next.addMessageListener(it) }
                oldSubscriptions.forEach { (topic, qos) ->
                    next.subscribe(topic, qos)
                }

                _instance = next
                return next
            }
        }

        fun connect(
            context: Context,
            serverUri: String? = null,
            clientId: String = "android-" + System.currentTimeMillis(),
            onConnected: (Boolean) -> Unit = {},
            onMessage: (String, String) -> Unit = { _, _ -> },
            onError: ((Throwable) -> Unit)? = null
        ) {
            val targetUri = serverUri ?: instance?.currentServerUri ?: DEFAULT_URI

            val h = when {
                _instance == null -> init(context, targetUri, clientId)
                _instance?.currentServerUri != targetUri -> switchServer(context, targetUri, clientId)
                else -> _instance!!
            }

            h.addMessageListener(onMessage)
            h.connect(
                onConnected = { onConnected(true) },
                onError = { e ->
                    onError?.invoke(e)
                    onConnected(false)
                }
            )
        }
    }
}
