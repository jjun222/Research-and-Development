package com.example.interfaceui.ui1

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.example.interfaceui.MqttHelper
import com.example.interfaceui.R
import com.example.interfaceui.broker.BrokerBootstrap
import com.example.interfaceui.util.setupToolbarBack
import org.json.JSONObject
import java.util.LinkedHashMap
import java.util.Locale
import kotlin.math.max

class SituationStatusActivity : AppCompatActivity() {

    private val DETECT_TTL_MS = 15000L

    data class Item(
        val key: String,
        val label: String,
        var detected: Boolean = false,
        var ts: Long = 0L
    )

    private val itemsOrder = listOf(
        "ALL_TRUE",
        "AI_fire_alert",
        "shz/sensor",
        "mq5/sensor",
        "mq7/sensor",
        "water_level/sensor",
        "doorbell/sensor"
    )

    private val items = LinkedHashMap<String, Item>()

    private lateinit var recycler: RecyclerView
    private val adapterRv = SituationAdapter()

    private var isScreenActive = false

    private val ttlHandler = Handler(Looper.getMainLooper())
    private val ttlRunnable = object : Runnable {
        override fun run() {
            val now = System.currentTimeMillis()
            var changed = false

            for ((k, item) in items) {
                if (k == "ALL_TRUE") continue

                if (item.detected && now - item.ts > DETECT_TTL_MS) {
                    item.detected = false
                    changed = true
                }
            }

            val before = items["ALL_TRUE"]?.detected ?: false

            updateAllTrue(now)

            val after = items["ALL_TRUE"]?.detected ?: false

            if (changed || before != after) {
                adapterRv.submit(listSnapshot())
            }

            ttlHandler.postDelayed(this, 1000L)
        }
    }

    private val mqttCb: (String, String) -> Unit = { topic, body ->
        if (topic.startsWith("interfaceui/logs/")) {
            handleServerLog(body)
        } else {
            handleSensorMessage(topic, body)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_situation_status)

        setupToolbarBack()

        recycler = findViewById(R.id.recycler)
        recycler.layoutManager = LinearLayoutManager(this)
        recycler.adapter = adapterRv

        initItems()
        adapterRv.submit(listSnapshot())
    }

    override fun onStart() {
        super.onStart()
        isScreenActive = true

        BrokerBootstrap.prepare(applicationContext) { result ->
            runOnUiThread {
                if (!isScreenActive) return@runOnUiThread

                if (!result.connected) {
                    Toast.makeText(
                        this,
                        result.errorMessage ?: "MQTT 연결 실패",
                        Toast.LENGTH_SHORT
                    ).show()
                    return@runOnUiThread
                }

                val helper = MqttHelper.instance ?: return@runOnUiThread

                helper.removeMessageListener(mqttCb)
                helper.addMessageListener(mqttCb)

                subscribeTopics(helper)
            }
        }

        ttlHandler.removeCallbacks(ttlRunnable)
        ttlHandler.postDelayed(ttlRunnable, 1000L)
    }

    override fun onStop() {
        isScreenActive = false
        MqttHelper.instance?.removeMessageListener(mqttCb)
        ttlHandler.removeCallbacks(ttlRunnable)
        super.onStop()
    }

    private fun initItems() {
        items.clear()

        items["ALL_TRUE"] = Item("ALL_TRUE", getString(R.string.sensor_all_true))
        items["AI_fire_alert"] = Item("AI_fire_alert", getString(R.string.sensor_ai_fire))
        items["shz/sensor"] = Item("shz/sensor", getString(R.string.sensor_shz))
        items["mq5/sensor"] = Item("mq5/sensor", getString(R.string.sensor_mq5))
        items["mq7/sensor"] = Item("mq7/sensor", getString(R.string.sensor_mq7))
        items["water_level/sensor"] = Item("water_level/sensor", getString(R.string.sensor_water))
        items["doorbell/sensor"] = Item("doorbell/sensor", getString(R.string.sensor_doorbell))

        items["gas/sensor"] = Item("gas/sensor", getString(R.string.sensor_mq5))
        items["co/sensor"] = Item("co/sensor", getString(R.string.sensor_mq7))
        items["flame/sensor"] = Item("flame/sensor", getString(R.string.sensor_shz))
        items["water-level/sensor"] = Item("water-level/sensor", getString(R.string.sensor_water))
        items["AI_D_fire"] = Item("AI_D_fire", getString(R.string.sensor_ai_fire))
    }

    private fun subscribeTopics(helper: MqttHelper) {
        listOf(
            "AI_fire_alert",
            "shz/sensor",
            "mq5/sensor",
            "mq7/sensor",
            "water_level/sensor",
            "doorbell/sensor",
            "gas/sensor",
            "co/sensor",
            "flame/sensor",
            "water-level/sensor",
            "AI_D_fire",
            "+/sensor",
            "interfaceui/logs/server/server",
            "interfaceui/logs/server/#"
        ).forEach { topic ->
            helper.subscribe(topic, qos = 1)
        }
    }

    private fun handleSensorMessage(topic: String, body: String) {
        val now = System.currentTimeMillis()
        val key = canonicalKey(topic) ?: return

        val detected = parseDetectedFlexible(body, key)

        items[key]?.let {
            it.detected = detected

            if (detected) {
                it.ts = now
            }
        }

        updateAllTrue(now)

        runOnUiThread {
            if (isScreenActive) {
                adapterRv.submit(listSnapshot())
            }
        }
    }

    private fun handleServerLog(body: String) {
        val now = System.currentTimeMillis()

        try {
            val j = JSONObject(body)
            val msg = j.optString("msg", "")
            val source = j.optString("source", "")
            val sensorId = j.optString("sensor_id", "")
            val topic = j.optString("topic", "")

            when {
                msg == "sensor event accepted" -> {
                    val key = canonicalKey(topic) ?: sensorIdToKey(sensorId) ?: return
                    items[key]?.let {
                        it.detected = true
                        it.ts = now
                    }
                }

                msg.contains("ALL-TRUE", ignoreCase = true) ||
                    msg == "neopixel fire alert sent" && source.contains("all_true", ignoreCase = true) ||
                    msg == "manual fire trigger received" ||
                    sensorId == "all_true" ||
                    sensorId == "manual_fire_test" -> {
                    items["ALL_TRUE"]?.let {
                        it.detected = true
                        it.ts = now
                    }
                }

                msg == "ALL-TRUE flags reset" ||
                    msg == "reset_all received" -> {
                    clearDetected()
                }
            }

            j.optJSONObject("sensor_status")?.let { snap ->
                val keys = snap.keys()

                while (keys.hasNext()) {
                    val id = keys.next()
                    val on = snap.optBoolean(id, false)
                    val key = sensorIdToKey(id) ?: continue

                    items[key]?.let { item ->
                        item.detected = on
                        if (on) item.ts = now
                    }
                }
            }

            updateAllTrue(now)

            runOnUiThread {
                if (isScreenActive) {
                    adapterRv.submit(listSnapshot())
                }
            }
        } catch (_: Exception) {
        }
    }

    private fun clearDetected() {
        items.values.forEach {
            it.detected = false
            it.ts = 0L
        }
    }

    private fun sensorIdToKey(id: String): String? = when (id.lowercase(Locale.getDefault())) {
        "shz_sensor_pico" -> "shz/sensor"
        "mq7_sensor_pico" -> "mq7/sensor"
        "mq5_sensor_pico", "gas_sensor_pico" -> "mq5/sensor"
        "ai_d_fire" -> "AI_fire_alert"
        "water_level_1", "waterlevel_1" -> "water_level/sensor"
        "doorbell_1" -> "doorbell/sensor"
        else -> null
    }

    private fun canonicalKey(raw: String): String? {
        val t = raw.lowercase(Locale.getDefault())

        return when {
            t.contains("ai_d_fire") ||
                t.contains("ai_fire_alert") ||
                t.contains("ai/fire") -> "AI_fire_alert"

            t.contains("shz") ||
                t.contains("flame/sensor") -> "shz/sensor"

            t == "gas/sensor" ||
                t.contains("mq5") -> "mq5/sensor"

            t == "co/sensor" ||
                t.contains("mq7") -> "mq7/sensor"

            t.contains("water-level") ||
                t.contains("water_level") -> "water_level/sensor"

            t.contains("doorbell") -> "doorbell/sensor"

            items.containsKey(raw) -> raw

            else -> null
        }
    }

    private fun updateAllTrue(now: Long) {
        val p = listOf(
            "AI_fire_alert",
            "shz/sensor",
            "mq5/sensor",
            "mq7/sensor"
        )

        val allOn = p.all { key ->
            val item = items[key]
            item != null && item.detected && (now - item.ts <= DETECT_TTL_MS)
        }

        val allItem = items["ALL_TRUE"] ?: return

        if (allOn) {
            allItem.detected = true
            allItem.ts = now
        } else if (now - allItem.ts > DETECT_TTL_MS) {
            allItem.detected = false
        }
    }

    private fun parseDetectedFlexible(body: String, key: String): Boolean {
        val t = body.trim()
        val lower = t.lowercase(Locale.getDefault())

        val textTrue = listOf(
            "1",
            "true",
            "on",
            "detected",
            "alert",
            "trigger",
            "위험",
            "감지",
            "fire",
            "flame",
            "gas",
            "co",
            "ring",
            "pressed",
            "bell",
            "wet",
            "overflow",
            "water",
            "leak",
            "flood"
        )

        val textFalse = listOf(
            "0",
            "false",
            "off",
            "normal",
            "clear",
            "정상"
        )

        if (lower in textTrue) return true
        if (lower in textFalse) return false

        t.toIntOrNull()?.let {
            return it != 0
        }

        return try {
            val j = JSONObject(t)

            val status = j.optString("status", "").lowercase(Locale.getDefault())
            val event = j.optString("event", "").lowercase(Locale.getDefault())

            if (status.contains("정상") ||
                status == "normal" ||
                status == "clear" ||
                status == "off"
            ) {
                return false
            }

            val commonTrue = listOf(
                "detected",
                "detect",
                "alert",
                "trigger",
                "위험",
                "감지",
                "fire",
                "flame",
                "gas",
                "co",
                "mq7",
                "mq5",
                "shz",
                "ring",
                "pressed",
                "bell",
                "water",
                "leak",
                "flood",
                "overflow",
                "wet"
            )

            val eventHit = commonTrue.any { event.contains(it) }
            val statusHit = commonTrue.any { status.contains(it) }

            val boolHit =
                j.optBoolean("detected", false) ||
                    j.optBoolean("alert", false) ||
                    j.optBoolean("pressed", false) ||
                    j.optBoolean("ring", false) ||
                    j.optBoolean("bell", false) ||
                    j.optBoolean("wet", false) ||
                    j.optBoolean("overflow", false)

            val intHit = listOf(
                "detected",
                "alert",
                "pressed",
                "ring",
                "bell",
                "wet",
                "overflow"
            ).any { field ->
                j.optInt(field, 0) == 1
            }

            val valueHit = if (j.has("value")) {
                j.optDouble("value", 0.0) != 0.0 &&
                    (
                        key == "water_level/sensor" ||
                            key == "doorbell/sensor" ||
                            statusHit ||
                            eventHit
                        )
            } else {
                false
            }

            boolHit || intHit || eventHit || statusHit || valueHit
        } catch (_: Exception) {
            false
        }
    }

    private fun listSnapshot(): List<Item> =
        itemsOrder.mapNotNull { key ->
            when (key) {
                "water_level/sensor" -> {
                    val a = items["water_level/sensor"]
                    val b = items["water-level/sensor"]

                    if (a == null && b == null) {
                        null
                    } else {
                        val latestTs = max((a?.ts ?: 0L), (b?.ts ?: 0L))
                        val detected = (a?.detected == true) || (b?.detected == true)

                        Item(
                            "water_level/sensor",
                            getString(R.string.sensor_water),
                            detected,
                            latestTs
                        )
                    }
                }

                else -> {
                    items[key]?.let { src ->
                        Item(src.key, src.label, src.detected, src.ts)
                    }
                }
            }
        }

    inner class SituationAdapter : RecyclerView.Adapter<SituationAdapter.VH>() {
        private var data: List<Item> = emptyList()

        inner class VH(view: android.view.View) : RecyclerView.ViewHolder(view) {
            val img: ImageView = view.findViewById(R.id.imgDot)
            val name: TextView = view.findViewById(R.id.tvName)
            val state: TextView = view.findViewById(R.id.tvState)
        }

        override fun onCreateViewHolder(parent: android.view.ViewGroup, viewType: Int): VH {
            val v = layoutInflater.inflate(R.layout.item_situation_status, parent, false)
            return VH(v)
        }

        override fun getItemCount(): Int = data.size

        override fun onBindViewHolder(holder: VH, position: Int) {
            val item = data[position]

            holder.name.text = item.label

            if (item.detected) {
                holder.img.setImageResource(R.drawable.dot_red)
                holder.state.text = getString(R.string.status_detected)
                holder.state.setTextColor(0xFFE74C3C.toInt())
            } else {
                holder.img.setImageResource(R.drawable.dot_green)
                holder.state.text = getString(R.string.status_normal)
                holder.state.setTextColor(0xFF2ECC71.toInt())
            }
        }

        fun submit(newData: List<Item>) {
            val old = data
            data = newData

            DiffUtil.calculateDiff(object : DiffUtil.Callback() {
                override fun getOldListSize() = old.size
                override fun getNewListSize() = newData.size

                override fun areItemsTheSame(o: Int, n: Int) =
                    old[o].key == newData[n].key

                override fun areContentsTheSame(o: Int, n: Int) =
                    old[o].detected == newData[n].detected &&
                        old[o].label == newData[n].label
            }).dispatchUpdatesTo(this)
        }
    }
}
