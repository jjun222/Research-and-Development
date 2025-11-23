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
import org.json.JSONObject
import java.util.*
import kotlin.math.max

class SituationStatusActivity : AppCompatActivity() {

    private val DETECT_TTL_MS = 8000L

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

    private val ttlHandler = Handler(Looper.getMainLooper())
    private val ttlRunnable = object : Runnable {
        override fun run() {
            val now = System.currentTimeMillis()
            var changed = false
            for ((k, it) in items) {
                if (k == "ALL_TRUE") continue
                if (it.detected && now - it.ts > DETECT_TTL_MS) {
                    it.detected = false
                    changed = true
                }
            }
            val before = items["ALL_TRUE"]?.detected ?: false
            updateAllTrue(now)
            val after = items["ALL_TRUE"]?.detected ?: false
            if (changed || before != after) adapterRv.submit(listSnapshot())
            ttlHandler.postDelayed(this, 1000L)
        }
    }

    // 멀티-리스너 등록/해제용 콜백
    private val mqttCb: (String, String) -> Unit = { topic, body ->
        if (topic.startsWith("interfaceui/logs/")) handleServerLog(body)
        else handleSensorMessage(topic, body)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_situation_status)

        recycler = findViewById(R.id.recycler)
        recycler.layoutManager = LinearLayoutManager(this)
        recycler.adapter = adapterRv

        // 초기 항목
        items["ALL_TRUE"]           = Item("ALL_TRUE", getString(R.string.sensor_all_true))
        items["AI_fire_alert"]      = Item("AI_fire_alert", getString(R.string.sensor_ai_fire))
        items["shz/sensor"]         = Item("shz/sensor", getString(R.string.sensor_shz))
        items["mq5/sensor"]         = Item("mq5/sensor", getString(R.string.sensor_mq5))
        items["mq7/sensor"]         = Item("mq7/sensor", getString(R.string.sensor_mq7))
        items["water_level/sensor"] = Item("water_level/sensor", getString(R.string.sensor_water))
        items["doorbell/sensor"]    = Item("doorbell/sensor", getString(R.string.sensor_doorbell))
        // 수신 전용 별칭(표시는 표준 키로 병합)
        items["gas/sensor"]         = Item("gas/sensor", getString(R.string.sensor_mq5))
        items["co/sensor"]          = Item("co/sensor", getString(R.string.sensor_mq7))
        items["flame/sensor"]       = Item("flame/sensor", getString(R.string.sensor_shz))
        items["water-level/sensor"] = Item("water-level/sensor", getString(R.string.sensor_water))
        items["AI_D_fire"]          = Item("AI_D_fire", getString(R.string.sensor_ai_fire))

        adapterRv.submit(listSnapshot())

        // MQTT 연결 + 구독
        MqttHelper.connect(
            context = applicationContext,
            onConnected = { ok ->
                runOnUiThread {
                    if (!ok) {
                        Toast.makeText(this, "MQTT 연결 실패", Toast.LENGTH_SHORT).show()
                    } else {
                        listOf(
                            // 표준
                            "AI_fire_alert",
                            "shz/sensor","mq5/sensor","mq7/sensor",
                            "water_level/sensor","doorbell/sensor",
                            // 별칭
                            "gas/sensor","co/sensor","flame/sensor","water-level/sensor",
                            "AI_D_fire",
                            // 와일드카드
                            "+/sensor",
                            // 서버 로그 스트림
                            "interfaceui/logs/server/server"
                        ).forEach { MqttHelper.instance?.subscribe(it, 1) }
                    }
                }
            },
            onMessage = mqttCb
        )

        ttlHandler.postDelayed(ttlRunnable, 1000L)
    }

    override fun onDestroy() {
        super.onDestroy()
        MqttHelper.instance?.removeMessageListener(mqttCb)
        ttlHandler.removeCallbacks(ttlRunnable)
    }

    // ── 센서 토픽 직접 처리 ─────────────────────────────
    private fun handleSensorMessage(topic: String, body: String) {
        val now = System.currentTimeMillis()
        val key = canonicalKey(topic) ?: return

        // ⚠️ MQ5/MQ7/SHZ/AI_fire는 서버 로그(accepted)만 신뢰하여 깜빡임 방지
        if (key == "mq5/sensor" || key == "mq7/sensor" || key == "shz/sensor" || key == "AI_fire_alert") {
            return
        }

        // doorbell, water_level 은 로컬 파싱으로 처리
        items[key]?.let {
            it.detected = parseDetectedFlexible(body)
            if (it.detected) it.ts = now
        }
        updateAllTrue(now)
        runOnUiThread { adapterRv.submit(listSnapshot()) }
    }

    // ── 서버 로그 파싱 (accepted / ALL-TRUE / reset / snapshot) ──
    private fun handleServerLog(body: String) {
        val now = System.currentTimeMillis()
        try {
            val j = JSONObject(body)
            val msg = j.optString("msg", "")

            when {
                msg == "sensor event accepted" -> {
                    val t = j.optString("topic", "")
                    val key = canonicalKey(t) ?: return
                    items[key]?.let { it.detected = true; it.ts = now }
                }
                msg.startsWith("ALL-TRUE", true) || msg.contains("ALL-TRUE detected", true) -> {
                    items["ALL_TRUE"]?.let { it.detected = true; it.ts = now }
                }
                msg == "ALL-TRUE flags reset" -> {
                    items["ALL_TRUE"]?.detected = false
                    listOf("AI_fire_alert","shz/sensor","mq5/sensor","mq7/sensor")
                        .forEach { k -> items[k]?.detected = false }
                }
            }

            // participates_in_alltrue 스냅샷 반영
            j.optJSONObject("sensor_status")?.let { snap ->
                val it = snap.keys()
                while (it.hasNext()) {
                    val id = it.next()
                    val on = snap.optBoolean(id, false)
                    val k = sensorIdToKey(id) ?: continue
                    items[k]?.let { item -> item.detected = on; item.ts = now }
                }
            }

            updateAllTrue(now)
            runOnUiThread { adapterRv.submit(listSnapshot()) }
        } catch (_: Exception) { /* ignore */ }
    }

    // 센서ID → 표준 키 매핑 (서버 로그용)
    private fun sensorIdToKey(id: String): String? = when (id.lowercase(Locale.getDefault())) {
        "shz_sensor_pico" -> "shz/sensor"
        "mq7_sensor_pico" -> "mq7/sensor"
        "mq5_sensor_pico", "gas_sensor_pico" -> "mq5/sensor"
        "ai_d_fire" -> "AI_fire_alert"
        // 필요 시 water/doorbell 기기 ID도 추가
        "water_level_1", "waterlevel_1" -> "water_level/sensor"
        "doorbell_1" -> "doorbell/sensor"
        else -> null
    }

    // 토픽 표준화
    private fun canonicalKey(raw: String): String? {
        val t = raw.lowercase(Locale.getDefault())
        return when {
            t.contains("ai_d_fire")      || t.contains("ai_fire_alert")  || t.contains("ai/fire")   -> "AI_fire_alert"
            t.contains("shz")            || t.contains("flame/sensor")                              -> "shz/sensor"
            t == "gas/sensor"            || t.contains("mq5")                                       -> "mq5/sensor"
            t == "co/sensor"             || t.contains("mq7")                                       -> "mq7/sensor"
            t.contains("water-level")    || t.contains("water_level")                               -> "water_level/sensor"
            t.contains("doorbell")                                                                      -> "doorbell/sensor"
            items.containsKey(raw) -> raw
            else -> null
        }
    }

    private fun updateAllTrue(now: Long) {
        val p = listOf("AI_fire_alert","shz/sensor","mq5/sensor","mq7/sensor")
        val allOn = p.all { k ->
            val it = items[k]
            it != null && it.detected && (now - it.ts <= DETECT_TTL_MS)
        }
        val allItem = items["ALL_TRUE"] ?: return
        if (allOn) { allItem.detected = true; allItem.ts = now }
        else if (now - allItem.ts > DETECT_TTL_MS) { allItem.detected = false }
    }

    /**
     * ✅ doorbell / water_level 계열을 폭넓게 감지
     * - 텍스트 페이로드: ring, bell, pressed, water, leak, flood, overflow, wet
     * - JSON:
     *    - event/status 에 위 단어 포함
     *    - pressed/ring/bell/wet/overflow 불리언 또는 정수 1
     *    - value 존재 + 위 키가 보이면 value != 0 → 감지
     */
    private fun parseDetectedFlexible(body: String): Boolean {
        val t = body.trim()
        val lower = t.lowercase(Locale.getDefault())

        // 텍스트 단일값
        val textTrue = listOf("1","true","on","detected","alert","ring","pressed","bell","wet","overflow","water","leak","flood")
        val textFalse = listOf("0","false","off","normal","clear")
        if (lower in textTrue) return true
        if (lower in textFalse) return false
        t.toIntOrNull()?.let { return it != 0 }

        return try {
            val j = JSONObject(t)

            fun anyKey(vararg keys: String): Boolean {
                val it = j.keys()
                while (it.hasNext()) {
                    val k = it.next().lowercase(Locale.getDefault())
                    if (keys.any { x -> k.contains(x) }) return true
                }
                return false
            }

            val event = j.optString("event", "").lowercase(Locale.getDefault())
            val status = j.optString("status", "").lowercase(Locale.getDefault())

            val eventHit = listOf("ring","pressed","bell","water","leak","flood","overflow","wet","trigger")
                .any { event.contains(it) }
            val statusHit = listOf("ring","pressed","bell","water","leak","flood","overflow","wet","alert","detected")
                .any { status.contains(it) }

            val boolHit = j.optBoolean("pressed", false) || j.optBoolean("ring", false) ||
                    j.optBoolean("bell", false)    || j.optBoolean("wet", false)  ||
                    j.optBoolean("overflow", false) || j.optBoolean("alert", false) ||
                    j.optBoolean("detected", false)

            val intHit  = listOf("pressed","ring","bell","wet","overflow","alert","detected")
                .any { j.optInt(it, 0) == 1 }

            // value 기반(doorbell/water 계열 키들이 보이면 0/비0로 판단)
            val hasDomainKey = anyKey("doorbell","bell","ring","water","leak","flood","overflow","wet")
            val valAsInt = if (j.has("value")) j.optInt("value", 0) else null
            val valueHit = hasDomainKey && (valAsInt != null) && (valAsInt != 0)

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
                    if (a == null && b == null) null else {
                        val latestTs = max((a?.ts ?: 0L), (b?.ts ?: 0L))
                        val detected = (a?.detected == true) || (b?.detected == true)
                        Item("water_level/sensor", getString(R.string.sensor_water), detected, latestTs)
                    }
                }
                else -> items[key]?.let { src ->
                    Item(src.key, src.label, src.detected, src.ts)
                }
            }
        }

    // ── RecyclerView Adapter ───────────────────────────
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
            val it = data[position]
            holder.name.text = it.label
            if (it.detected) {
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
                override fun areItemsTheSame(o: Int, n: Int) = old[o].key == newData[n].key
                override fun areContentsTheSame(o: Int, n: Int) =
                    old[o].detected == newData[n].detected && old[o].label == newData[n].label
            }).dispatchUpdatesTo(this)
        }
    }
}
