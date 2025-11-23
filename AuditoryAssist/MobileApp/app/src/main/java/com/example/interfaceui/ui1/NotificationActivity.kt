package com.example.interfaceui.ui1

import android.os.Bundle
import android.view.View
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.example.interfaceui.R
import com.example.interfaceui.adapter.NotificationAdapter
import com.example.interfaceui.data.AppDatabase
import com.example.interfaceui.data.NotificationEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class NotificationActivity : AppCompatActivity() {

    private lateinit var adapter: NotificationAdapter
    private lateinit var tvEmpty: View
    private val dao by lazy { AppDatabase.getDatabase(applicationContext).notificationDao() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_notification)

        // Toolbar(뒤로가기)
        findViewById<androidx.appcompat.widget.Toolbar>(R.id.toolbar)?.apply {
            title = getString(R.string.notification_title)
            setNavigationIcon(R.drawable.ic_arrow_back_24)
            setNavigationOnClickListener { onBackPressedDispatcher.onBackPressed() }
        }

        // RecyclerView
        val rv = findViewById<androidx.recyclerview.widget.RecyclerView>(R.id.recyclerView)
        tvEmpty = findViewById(R.id.tvEmpty)
        adapter = NotificationAdapter()
        rv.layoutManager = LinearLayoutManager(this)
        rv.adapter = adapter

        // 최초 로드
        refreshList()

        // (선택) 테스트용 추가 버튼이 있으면 더미 데이터 삽입
        findViewById<View?>(R.id.btnAdd)?.setOnClickListener {
            lifecycleScope.launch(Dispatchers.IO) {
                dao.insert(
                    NotificationEntity(
                        title = getString(R.string.sample_title),
                        message = getString(R.string.sample_body)
                    )
                )
                refreshList()
            }
        }

        // 전체 삭제
        findViewById<View?>(R.id.btnClear)?.setOnClickListener {
            lifecycleScope.launch(Dispatchers.IO) {
                dao.deleteAll()
                refreshList()
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // 화면 재진입 시 DB 최신 내용 반영
        refreshList()
    }

    private fun refreshList() {
        lifecycleScope.launch(Dispatchers.IO) {
            val items: List<NotificationEntity> = dao.getAll() // suspend fun 가정
            launch(Dispatchers.Main) {
                adapter.submit(items)
                tvEmpty.visibility = if (items.isEmpty()) View.VISIBLE else View.GONE
            }
        }
    }
}
