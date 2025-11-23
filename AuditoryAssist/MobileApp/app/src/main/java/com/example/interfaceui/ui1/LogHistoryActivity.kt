// app/src/main/java/com/example/interfaceui/ui1/LogHistoryActivity.kt
package com.example.interfaceui.ui1

import android.annotation.SuppressLint
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.content.res.AppCompatResources
import androidx.core.view.isVisible
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.example.interfaceui.R
import com.example.interfaceui.data.DevicePrefs
import com.example.interfaceui.data.LogStore
import java.text.SimpleDateFormat
import java.util.Locale

class LogHistoryActivity : AppCompatActivity() {

    private lateinit var tvTitle: TextView
    private lateinit var tvEmpty: TextView
    private lateinit var recycler: RecyclerView
    private lateinit var btnMore: Button
    private val adapter by lazy { LogAdapter() }

    private var targetId: String = "ALL"
    private var targetType: String = "all"

    private val sdf = SimpleDateFormat("HH:mm:ss", Locale.getDefault())

    private val items = mutableListOf<LogItem>()     // 오래된→최신
    private var cursorTs: Long = Long.MAX_VALUE      // 다음 조회용 before_ts

    @SuppressLint("SetTextI18n")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_log_history)

        // 툴바: 아이콘 강제 지정 + 동작 연결
        (findViewById<View>(R.id.toolbar) as? androidx.appcompat.widget.Toolbar)?.apply {
            navigationIcon = AppCompatResources.getDrawable(
                this@LogHistoryActivity, R.drawable.ic_arrow_back_24
            )
            setNavigationOnClickListener { onBackPressedDispatcher.onBackPressed() }
        }

        tvTitle  = findViewById(R.id.tvTitle)
        tvEmpty  = findViewById(R.id.tvEmpty)
        recycler = findViewById(R.id.recycler)
        btnMore  = findViewById(R.id.btnLoadMore)

        recycler.layoutManager = LinearLayoutManager(this)
        recycler.adapter = adapter

        targetId   = intent.getStringExtra("target_id") ?: "ALL"
        targetType = intent.getStringExtra("target_type") ?: "all"
        tvTitle.text = when (targetType) {
            "all"        -> "이전 로그 (ALL)"
            "server"     -> "이전 로그 (server)"
            "subscriber" -> "이전 로그 ($targetId)"
            else         -> "이전 로그"
        }

        btnMore.setOnClickListener { loadMore() }

        onBackPressedDispatcher.addCallback(this, object: OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = finish()
        })
    }

    override fun onStart() {
        super.onStart()
        cursorTs = Long.MAX_VALUE
        items.clear()
        loadMore()
    }

    private fun loadMore() {
        val batch = when (targetType) {
            "all"        -> LogStore.queryOlderAll(cursorTs, 200)
            "server"     -> LogStore.queryOlder("server", "server", cursorTs, 200)
            "subscriber" -> LogStore.queryOlder("subscriber", targetId, cursorTs, 200)
            else -> emptyList()
        }
        if (batch.isEmpty()) {
            render(); return
        }
        items.addAll(batch.map { LogItem(it.type, it.id, it.level, it.msg, it.ts) })
        cursorTs = batch.first().ts
        render()
    }

    private fun render() {
        val list = items.toList()
        tvEmpty.isVisible = list.isEmpty()
        adapter.submitList(list)
        if (list.isNotEmpty()) recycler.scrollToPosition(list.lastIndex)
    }

    // ===== 데이터 & 어댑터 =====
    private data class LogItem(val type: String, val id: String,
                               val level: String, val msg: String, val ts: Long)

    private inner class LogAdapter :
        ListAdapter<LogItem, VH>(object: DiffUtil.ItemCallback<LogItem>() {
            override fun areItemsTheSame(o: LogItem, n: LogItem) =
                o.ts == n.ts && o.msg == n.msg && o.type == n.type && o.id == n.id
            override fun areContentsTheSame(o: LogItem, n: LogItem) = o == n
        }) {
        override fun onCreateViewHolder(p: android.view.ViewGroup, vt: Int): VH {
            val v = layoutInflater.inflate(R.layout.item_log, p, false)
            return VH(v)
        }
        override fun onBindViewHolder(h: VH, pos: Int) = h.bind(getItem(pos))
    }

    private inner class VH(v: View) : RecyclerView.ViewHolder(v) {
        private val tvLine = v.findViewById<TextView>(R.id.tvLine)
        fun bind(it: LogItem) {
            val t = sdf.format(it.ts * 1000)
            val who = if (it.type == "server") "server" else it.id
            tvLine.text = "[$t][$who][${it.level}] ${it.msg}"
        }
    }
}
