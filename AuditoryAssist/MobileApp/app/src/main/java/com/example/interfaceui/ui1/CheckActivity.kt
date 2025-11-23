package com.example.interfaceui.ui1

import android.graphics.PorterDuff
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.example.interfaceui.MqttHelper
import com.example.interfaceui.R
import com.example.interfaceui.alias.DeviceAlias
import org.json.JSONObject

class CheckActivity : AppCompatActivity() {

    // ▶ 신선도 TTL(초): 디바이스 45초, 서버 180초
    private val TTL_DEVICE_SEC = 45L
    private val TTL_SERVER_SEC = 180L

    private lateinit var recycler: RecyclerView
    private val adapter by lazy { StatusAdapter() }

    // canonicalId -> Status
    private val map = linkedMapOf<String, NodeStatus>()

    // 1초마다 신선도 체크
    private val tickHandler = Handler(Looper.getMainLooper())
    private val tick = object : Runnable {
        override fun run() {
            adapter.notifyDataSetChanged()
            tickHandler.postDelayed(this, 1000L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_check)

        findViewById<androidx.appcompat.widget.Toolbar?>(R.id.toolbar)?.also {
            it.setNavigationOnClickListener { finish() }
        }

        recycler = findViewById(R.id.recycler)
        recycler.adapter = adapter

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = finish()
        })
    }

    override fun onStart() {
        super.onStart()

        tickHandler.post(tick)

        MqttHelper.connect(
            context = applicationContext,
            onConnected = { ok ->
                runOnUiThread {
                    if (!ok) Toast.makeText(this, "MQTT 연결 실패", Toast.LENGTH_SHORT).show()
                }
                if (ok) {
                    MqttHelper.instance?.subscribe("interfaceui/status/#", qos = 1)
                }
            },
            onMessage = { topic, payload ->
                if (!topic.startsWith("interfaceui/status/")) return@connect
                val status = parseStatus(topic, payload)
                // ⬇︎ 화이트리스트 밖(ID 미허용)은 표시 안 함 (Neopixel_3/4 등 숨김)
                if (!DeviceAlias.shouldShow(status.id)) return@connect
                runOnUiThread { upsert(status) }
            }
        )
    }

    override fun onStop() {
        super.onStop()
        tickHandler.removeCallbacks(tick)
    }

    /** 토픽 + JSON/텍스트 → NodeStatus
     *  - ts_ms(밀리초) → 초로 환산
     *  - ts 없으면 0
     *  - 항상 수신시각(seenSec)을 현재초로 기록 → 텍스트 "online"만 와도 신선도 판단 가능
     */
    private fun parseStatus(topic: String, raw: String): NodeStatus {
        val parts = topic.split('/')
        val type = (parts.getOrNull(2) ?: "unknown").lowercase()
        val tail = parts.drop(3).joinToString("/")

        val fallbackIdRaw = if (type == "server") "server" else (if (tail.isEmpty()) "unknown" else tail)
        val fallbackId = DeviceAlias.canonicalId(fallbackIdRaw)
        val nowSec = System.currentTimeMillis() / 1000

        return try {
            val j = JSONObject(raw)
            val idRaw = j.optString("id", fallbackId)
            val id = DeviceAlias.canonicalId(idRaw)

            val defaultName = when (type) {
                "server"     -> "MQTT 판단 서버"
                "publisher"  -> if (id.isEmpty()) "Publisher" else id
                "subscriber" -> if (id.isEmpty()) "Subscriber" else id
                else         -> if (id.isEmpty()) "Unknown" else id
            }
            val nameFromPayload = j.optString("name", defaultName)

            val online = j.optString("status", "")
                .equals("online", true) || j.optBoolean("online", false)

            // ts_ms(밀리초) 우선, 없으면 ts(초), 둘 다 없으면 0
            val tsMs  = if (j.has("ts_ms")) j.optLong("ts_ms", 0L) else 0L
            val tsSec = when {
                tsMs > 0L -> tsMs / 1000L
                j.has("ts") -> j.optLong("ts", 0L)
                else -> 0L
            }

            NodeStatus(
                id = id,
                nameOrig = nameFromPayload,
                type = type,
                online = online,
                tsSec = tsSec,
                seenSec = nowSec
            )
        } catch (_: Exception) {
            val online = raw.equals("online", true)
            NodeStatus(
                id = fallbackId,
                nameOrig = when (type) {
                    "server"     -> "MQTT 판단 서버"
                    "publisher"  -> fallbackId
                    "subscriber" -> fallbackId
                    else         -> fallbackId
                },
                type = type,
                online = online,
                tsSec = 0L,
                seenSec = nowSec
            )
        }
    }

    /** 수신 시각은 항상 갱신. tsSec=0으로 온 업데이트는 이전 tsSec 유지 */
    private fun upsert(s: NodeStatus) {
        val key = s.key()
        val old = map[key]
        val merged = if (old != null) {
            s.copy(tsSec = if (s.tsSec == 0L) old.tsSec else s.tsSec)  // 원격 ts 보존
        } else s
        map[key] = merged
        adapter.submitList(map.values.toList())
    }

    // ===== Adapter =====
    private inner class StatusAdapter :
        ListAdapter<NodeStatus, StatusVH>(object : DiffUtil.ItemCallback<NodeStatus>() {
            override fun areItemsTheSame(o: NodeStatus, n: NodeStatus) = o.key() == n.key()
            override fun areContentsTheSame(o: NodeStatus, n: NodeStatus) = o == n
        }) {
        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): StatusVH {
            val v = LayoutInflater.from(parent.context).inflate(R.layout.item_status, parent, false)
            return StatusVH(v)
        }
        override fun onBindViewHolder(h: StatusVH, pos: Int) = h.bind(getItem(pos))
    }

    private inner class StatusVH(v: View) : RecyclerView.ViewHolder(v) {
        private val tvName = v.findViewById<TextView>(R.id.tvName)
        private val tvDesc = v.findViewById<TextView>(R.id.tvDesc)
        private val tvStatus = v.findViewById<TextView>(R.id.tvStatus)
        private val dot = v.findViewById<View>(R.id.dot)

        fun bind(s: NodeStatus) {
            // 표시는 항상 별칭 우선
            val displayName = DeviceAlias.resolve(itemView.context, s.id, s.nameOrig)
            tvName.text = displayName
            tvDesc.text = "${s.type} / ${s.id}"

            val ctx = itemView.context
            val green = ContextCompat.getColor(ctx, android.R.color.holo_green_light)
            val red   = ContextCompat.getColor(ctx, android.R.color.holo_red_light)

            val nowSec = System.currentTimeMillis() / 1000
            val ttl = if (s.type == "server") TTL_SERVER_SEC else TTL_DEVICE_SEC

            // ▶ 신선도 판단: tsSec(원격시간)과 seenSec(수신시간) 중 '더 최신' 기준
            val basis = maxOf(s.tsSec, s.seenSec)
            val fresh = (basis > 0L) && (nowSec - basis <= ttl)
            val showOnline = s.online && fresh

            val (txt, color) = if (showOnline) "온라인" to green else "오프라인" to red
            tvStatus.text = txt
            dot.background.setColorFilter(color, PorterDuff.Mode.SRC_IN)
        }
    }

    private data class NodeStatus(
        val id: String,
        val nameOrig: String,   // payload에서 온 원래 이름(별칭 적용 전)
        val type: String,       // "server" | "publisher" | "subscriber" | "unknown"
        val online: Boolean,
        val tsSec: Long,        // 원격에서 보낸 Unix seconds (0=없음)
        val seenSec: Long       // 클라이언트가 마지막으로 이 노드를 "봤던" 시각(초)
    ) {
        fun key() = "${type.lowercase()}|$id"  // canonicalId 기반
    }
}
