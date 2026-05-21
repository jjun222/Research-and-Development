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
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.example.interfaceui.MqttHelper
import com.example.interfaceui.R
import com.example.interfaceui.alias.DeviceAlias
import com.example.interfaceui.broker.BrokerBootstrap
import com.example.interfaceui.util.setupToolbarBack
import org.json.JSONObject

class CheckActivity : AppCompatActivity() {

    private val TTL_DEVICE_SEC = 45L
    private val TTL_SERVER_SEC = 180L

    private lateinit var recycler: RecyclerView
    private val adapter by lazy { StatusAdapter() }

    private val map = linkedMapOf<String, NodeStatus>()

    private var isScreenActive = false

    private val tickHandler = Handler(Looper.getMainLooper())
    private val tick = object : Runnable {
        override fun run() {
            adapter.notifyDataSetChanged()
            tickHandler.postDelayed(this, 1000L)
        }
    }

    private val statusListener: (String, String) -> Unit = listener@{ topic, payload ->
        if (!topic.startsWith("interfaceui/status/")) return@listener

        val status = parseStatus(topic, payload)

        if (!DeviceAlias.shouldShow(status.id)) return@listener

        runOnUiThread {
            if (isScreenActive) {
                upsert(status)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_check)

        setupToolbarBack()

        recycler = findViewById(R.id.recycler)
        recycler.adapter = adapter
    }

    override fun onStart() {
        super.onStart()
        isScreenActive = true

        tickHandler.removeCallbacks(tick)
        tickHandler.post(tick)

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

                helper.removeMessageListener(statusListener)
                helper.addMessageListener(statusListener)

                helper.subscribe("interfaceui/status/#", qos = 1)
            }
        }
    }

    override fun onStop() {
        isScreenActive = false
        MqttHelper.instance?.removeMessageListener(statusListener)
        tickHandler.removeCallbacks(tick)
        super.onStop()
    }

    private fun parseStatus(topic: String, raw: String): NodeStatus {
        val parts = topic.split('/')
        val type = (parts.getOrNull(2) ?: "unknown").lowercase()
        val tail = parts.drop(3).joinToString("/")

        val fallbackIdRaw = if (type == "server") {
            "server"
        } else {
            if (tail.isEmpty()) "unknown" else tail
        }

        val fallbackId = DeviceAlias.canonicalId(fallbackIdRaw)
        val nowSec = System.currentTimeMillis() / 1000

        return try {
            val j = JSONObject(raw)
            val idRaw = j.optString("id", fallbackId)
            val id = DeviceAlias.canonicalId(idRaw)

            val defaultName = when (type) {
                "server" -> "MQTT 판단 서버"
                "publisher" -> if (id.isEmpty()) "Publisher" else id
                "subscriber" -> if (id.isEmpty()) "Subscriber" else id
                else -> if (id.isEmpty()) "Unknown" else id
            }

            val nameFromPayload = j.optString("name", defaultName)

            val online = j.optString("status", "")
                .equals("online", true) || j.optBoolean("online", false)

            val tsMs = if (j.has("ts_ms")) j.optLong("ts_ms", 0L) else 0L
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
                    "server" -> "MQTT 판단 서버"
                    "publisher" -> fallbackId
                    "subscriber" -> fallbackId
                    else -> fallbackId
                },
                type = type,
                online = online,
                tsSec = 0L,
                seenSec = nowSec
            )
        }
    }

    private fun upsert(s: NodeStatus) {
        val key = s.key()
        val old = map[key]

        val merged = if (old != null) {
            s.copy(tsSec = if (s.tsSec == 0L) old.tsSec else s.tsSec)
        } else {
            s
        }

        map[key] = merged
        adapter.submitList(map.values.toList())
    }

    private inner class StatusAdapter :
        ListAdapter<NodeStatus, StatusVH>(object : DiffUtil.ItemCallback<NodeStatus>() {
            override fun areItemsTheSame(o: NodeStatus, n: NodeStatus) = o.key() == n.key()
            override fun areContentsTheSame(o: NodeStatus, n: NodeStatus) = o == n
        }) {

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): StatusVH {
            val v = LayoutInflater.from(parent.context)
                .inflate(R.layout.item_status, parent, false)
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
            val displayName = DeviceAlias.resolve(itemView.context, s.id, s.nameOrig)

            tvName.text = displayName
            tvDesc.text = "${s.type} / ${s.id}"

            val ctx = itemView.context
            val green = ContextCompat.getColor(ctx, android.R.color.holo_green_light)
            val red = ContextCompat.getColor(ctx, android.R.color.holo_red_light)

            val nowSec = System.currentTimeMillis() / 1000
            val ttl = if (s.type == "server") TTL_SERVER_SEC else TTL_DEVICE_SEC

            val basis = maxOf(s.tsSec, s.seenSec)
            val fresh = (basis > 0L) && (nowSec - basis <= ttl)
            val showOnline = s.online && fresh

            val (txt, color) = if (showOnline) {
                "온라인" to green
            } else {
                "오프라인" to red
            }

            tvStatus.text = txt
            dot.background.setColorFilter(color, PorterDuff.Mode.SRC_IN)
        }
    }

    private data class NodeStatus(
        val id: String,
        val nameOrig: String,
        val type: String,
        val online: Boolean,
        val tsSec: Long,
        val seenSec: Long
    ) {
        fun key() = "${type.lowercase()}|$id"
    }
}
