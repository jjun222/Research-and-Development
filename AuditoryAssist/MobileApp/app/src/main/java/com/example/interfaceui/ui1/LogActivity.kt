// app/src/main/java/com/example/interfaceui/ui1/LogActivity.kt
package com.example.interfaceui.ui1

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.*
import android.widget.*
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
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
import java.util.concurrent.Executors

class LogActivity : AppCompatActivity() {

    private lateinit var spinner: Spinner
    private lateinit var btnHistory: Button
    private lateinit var tvEmpty: TextView
    private lateinit var recycler: RecyclerView
    private val adapter by lazy { LogAdapter() }

    private var currentFilter: Filter = Filter.All

    private val sdf = SimpleDateFormat("HH:mm:ss", Locale.getDefault())

    // DB 폴링(가벼운 주기 갱신)
    private val uiHandler = Handler(Looper.getMainLooper())
    private val bg = Executors.newSingleThreadExecutor()
    private val pollMs = 1000L
    private val pollTask = object : Runnable {
        override fun run() {
            loadLatestAndRender()
            uiHandler.postDelayed(this, pollMs)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_log)

        findViewById<View>(R.id.toolbar)?.let { tb ->
            (tb as? androidx.appcompat.widget.Toolbar)?.setNavigationOnClickListener { finish() }
        }

        spinner    = findViewById(R.id.spnSensors)
        btnHistory = findViewById(R.id.btnHistory)
        tvEmpty    = findViewById(R.id.tvEmpty)
        recycler   = findViewById(R.id.recycler)

        recycler.layoutManager = LinearLayoutManager(this).apply { stackFromEnd = true }
        recycler.adapter = adapter

        val items = mutableListOf<String>().apply {
            add("ALL"); add("server"); addAll(DevicePrefs.getDevices(this@LogActivity))
        }
        spinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, items)
        spinner.setSelection(0)

        spinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>, v: View?, pos: Int, id: Long) {
                currentFilter = spinnerToFilter()
                loadLatestAndRender()
            }
            override fun onNothingSelected(parent: AdapterView<*>) {}
        }

        btnHistory.setOnClickListener { openHistoryScreen() }

        onBackPressedDispatcher.addCallback(this, object: OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = finish()
        })
    }

    override fun onStart() {
        super.onStart()
        // 화면 들어오면 폴링 시작
        uiHandler.post(pollTask)
    }

    override fun onStop() {
        super.onStop()
        // 화면 나가면 폴링 중단
        uiHandler.removeCallbacks(pollTask)
    }

    private fun spinnerToFilter(): Filter {
        val s = spinner.selectedItem?.toString()?.trim().orEmpty()
        return when {
            s.equals("ALL", true)    -> Filter.All
            s.equals("server", true) -> Filter.Server
            else                     -> Filter.Device(s)
        }
    }

    private fun loadLatestAndRender() {
        val f = currentFilter
        bg.execute {
            val recs = when (f) {
                is Filter.All    -> LogStore.queryLatestAll(20)
                is Filter.Server -> LogStore.queryLatest("server", "server", 20)
                is Filter.Device -> LogStore.queryLatest("subscriber", f.id, 20)
            }
            val list = recs.map { LogItem(it.type, it.id, it.level, it.msg, it.ts) }
            runOnUiThread {
                tvEmpty.isVisible = list.isEmpty()
                adapter.submitList(list)
                if (list.isNotEmpty()) recycler.scrollToPosition(list.lastIndex)
            }
        }
    }

    // ===== 데이터/어댑터 =====
    private data class LogItem(val type: String, val id: String,
                               val level: String, val msg: String, val ts: Long)
    private sealed interface Filter {
        data object All : Filter
        data object Server : Filter
        data class Device(val id: String) : Filter
    }

    private inner class LogAdapter :
        ListAdapter<LogItem, VH>(object: DiffUtil.ItemCallback<LogItem>() {
            override fun areItemsTheSame(o: LogItem, n: LogItem) =
                o.ts == n.ts && o.msg == n.msg && o.type == n.type && o.id == n.id
            override fun areContentsTheSame(o: LogItem, n: LogItem) = o == n
        }) {
        override fun onCreateViewHolder(p: ViewGroup, vt: Int): VH {
            val v = layoutInflater.inflate(R.layout.item_log, p, false)
            return VH(v)
        }
        override fun onBindViewHolder(h: VH, pos: Int) = h.bind(getItem(pos))
    }

    private inner class VH(v: View): RecyclerView.ViewHolder(v) {
        private val tvLine = v.findViewById<TextView>(R.id.tvLine)
        fun bind(it: LogItem) {
            val t = sdf.format(it.ts * 1000)
            val who = if (it.type == "server") "server" else it.id
            tvLine.text = "[$t][$who][${it.level}] ${it.msg}"
        }
    }

    /** 이전 로그 화면 열기 */
    private fun openHistoryScreen() {
        val intent = Intent(this, LogHistoryActivity::class.java).apply {
            when (val f = spinnerToFilter()) {
                is Filter.All    -> { putExtra("target_id", "ALL");    putExtra("target_type", "all") }
                is Filter.Server -> { putExtra("target_id", "server"); putExtra("target_type", "server") }
                is Filter.Device -> { putExtra("target_id", f.id);     putExtra("target_type", "subscriber") }
            }
        }
        startActivity(intent)
    }
}
