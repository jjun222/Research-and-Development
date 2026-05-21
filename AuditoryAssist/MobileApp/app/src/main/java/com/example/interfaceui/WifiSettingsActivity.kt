package com.example.interfaceui

import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.example.interfaceui.broker.BrokerPrefs
import com.example.interfaceui.databinding.ActivityWifiSettingsBinding
import com.example.interfaceui.net.BrokerDiscovery
import com.example.interfaceui.net.BrokerInfo
import com.example.interfaceui.service.PushAlertService
import com.example.interfaceui.util.setupToolbarBack
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class WifiSettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivityWifiSettingsBinding

    private val prefs by lazy {
        getSharedPreferences("broker_discovery", MODE_PRIVATE)
    }

    private val PREF_KEY_HISTORY = "broker_history"
    private val PREF_KEY_HOST = "broker_host"
    private val PREF_KEY_PORT = "broker_port"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        binding = ActivityWifiSettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setupToolbarBack()

        loadHistory()

        binding.btnDiscoverBroker.setOnClickListener {
            startBrokerDiscovery()
        }

        binding.btnClearLog.setOnClickListener {
            clearHistory()
        }
    }

    private fun startBrokerDiscovery() {
        binding.btnDiscoverBroker.isEnabled = false
        Toast.makeText(this, "브로커 탐색 중...", Toast.LENGTH_SHORT).show()

        BrokerDiscovery.discoverAll(timeoutMs = 1500) { brokers ->
            runOnUiThread {
                binding.btnDiscoverBroker.isEnabled = true

                if (brokers.isEmpty()) {
                    Toast.makeText(
                        this,
                        "브로커를 찾지 못했습니다.",
                        Toast.LENGTH_SHORT
                    ).show()
                    return@runOnUiThread
                }

                appendDiscoveryHistory(brokers)
                showBrokerListDialog(brokers)
            }
        }
    }

    private fun showBrokerListDialog(brokers: List<BrokerInfo>) {
        val items = brokers.map { it.displayName }.toTypedArray()

        AlertDialog.Builder(this)
            .setTitle("발견된 브로커 목록")
            .setItems(items) { _, which ->
                val selected = brokers[which]
                showConnectConfirmDialog(selected)
            }
            .setNegativeButton("닫기", null)
            .show()
    }

    private fun showConnectConfirmDialog(info: BrokerInfo) {
        val message = buildString {
            append("해당 브로커와 연결 하시겠습니까?")
            append("\n\n")

            if (!info.name.isNullOrBlank()) {
                append("이름: ${info.name}\n")
            }

            append("IP: ${info.ip}\n")
            append("PORT: ${info.port}")
        }

        AlertDialog.Builder(this)
            .setTitle("브로커 연결")
            .setMessage(message)
            .setPositiveButton("확인") { _, _ ->
                connectToSelectedBroker(info)
            }
            .setNegativeButton("취소", null)
            .show()
    }

    private fun connectToSelectedBroker(info: BrokerInfo) {
        binding.btnDiscoverBroker.isEnabled = false

        val helper = MqttHelper.switchServer(applicationContext, info.serverUri)

        helper.connect(
            onConnected = {
                binding.btnDiscoverBroker.isEnabled = true

                BrokerPrefs.saveBrokerUri(applicationContext, info.serverUri)

                prefs.edit()
                    .putString(PREF_KEY_HOST, info.ip)
                    .putInt(PREF_KEY_PORT, info.port)
                    .apply()

                appendConnectionHistory(info)

                PushAlertService.ensureTokenRegistered(applicationContext)

                Toast.makeText(
                    this,
                    "브로커 연결 완료: ${info.ip}:${info.port}",
                    Toast.LENGTH_SHORT
                ).show()
            },
            onError = {
                binding.btnDiscoverBroker.isEnabled = true

                Toast.makeText(
                    this,
                    "브로커 연결 실패: ${info.ip}:${info.port}",
                    Toast.LENGTH_SHORT
                ).show()
            }
        )
    }

    private fun loadHistory() {
        val history = prefs.getString(PREF_KEY_HISTORY, "") ?: ""
        binding.txtBrokerHistory.text = history

        binding.scrollHistory.post {
            binding.scrollHistory.fullScroll(View.FOCUS_DOWN)
        }
    }

    private fun appendDiscoveryHistory(brokers: List<BrokerInfo>) {
        val now = currentTime()

        val lines = brokers.joinToString("\n") { broker ->
            "[$now] 브로커 발견: ${broker.ip}:${broker.port}"
        }

        val old = binding.txtBrokerHistory.text?.toString().orEmpty()
        val newText = if (old.isBlank()) lines else "$old\n$lines"

        binding.txtBrokerHistory.text = newText

        binding.scrollHistory.post {
            binding.scrollHistory.fullScroll(View.FOCUS_DOWN)
        }

        prefs.edit()
            .putString(PREF_KEY_HISTORY, newText)
            .apply()
    }

    private fun appendConnectionHistory(info: BrokerInfo) {
        val line = "[${currentTime()}] 브로커 연결 완료: ${info.ip}:${info.port}"

        val old = binding.txtBrokerHistory.text?.toString().orEmpty()
        val newText = if (old.isBlank()) line else "$old\n$line"

        binding.txtBrokerHistory.text = newText

        binding.scrollHistory.post {
            binding.scrollHistory.fullScroll(View.FOCUS_DOWN)
        }

        prefs.edit()
            .putString(PREF_KEY_HISTORY, newText)
            .apply()
    }

    private fun clearHistory() {
        binding.txtBrokerHistory.text = ""

        prefs.edit()
            .remove(PREF_KEY_HISTORY)
            .remove(PREF_KEY_HOST)
            .remove(PREF_KEY_PORT)
            .apply()

        Toast.makeText(this, "브로커 발견 기록을 지웠습니다.", Toast.LENGTH_SHORT).show()
    }

    private fun currentTime(): String {
        return SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
    }
}
