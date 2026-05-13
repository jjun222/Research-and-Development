package com.example.interfaceui.ui1

import android.os.Bundle
import android.view.View
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.example.interfaceui.R
import com.example.interfaceui.adapter.NotificationAdapter
import com.example.interfaceui.data.AppDatabase
import com.example.interfaceui.data.NotificationEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

class NotificationActivity : AppCompatActivity() {

    private lateinit var adapter: NotificationAdapter
    private lateinit var tvEmpty: View
    private lateinit var swipeRefresh: SwipeRefreshLayout

    private val dao by lazy {
        AppDatabase.getDatabase(applicationContext).notificationDao()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_notification)

        findViewById<androidx.appcompat.widget.Toolbar>(R.id.toolbar)?.apply {
            title = getString(R.string.notifications_title)
            setNavigationIcon(R.drawable.ic_arrow_back_24)
            setNavigationOnClickListener {
                onBackPressedDispatcher.onBackPressed()
            }
        }

        val rv = findViewById<androidx.recyclerview.widget.RecyclerView>(R.id.recyclerView)
        tvEmpty = findViewById(R.id.tvEmpty)
        swipeRefresh = findViewById(R.id.swipeRefresh)

        adapter = NotificationAdapter()
        rv.layoutManager = LinearLayoutManager(this)
        rv.adapter = adapter

        swipeRefresh.setOnRefreshListener {
            swipeRefresh.isRefreshing = false
        }

        observeNotifications()

        findViewById<View?>(R.id.btnAdd)?.setOnClickListener {
            lifecycleScope.launch(Dispatchers.IO) {
                dao.insert(
                    NotificationEntity(
                        title = getString(R.string.sample_title),
                        message = getString(R.string.sample_body),
                        createdAt = System.currentTimeMillis()
                    )
                )
            }
        }

        findViewById<View?>(R.id.btnClear)?.setOnClickListener {
            lifecycleScope.launch(Dispatchers.IO) {
                dao.deleteAll()
            }
        }
    }

    private fun observeNotifications() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                dao.observeAll().collectLatest { items ->
                    adapter.submit(items)
                    tvEmpty.visibility = if (items.isEmpty()) View.VISIBLE else View.GONE
                    swipeRefresh.isRefreshing = false
                }
            }
        }
    }
}
