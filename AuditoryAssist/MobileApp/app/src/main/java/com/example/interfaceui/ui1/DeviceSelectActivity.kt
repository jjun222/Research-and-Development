package com.example.interfaceui.ui1

import android.app.AlertDialog
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.Spinner
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.example.interfaceui.MqttHelper
import com.example.interfaceui.R
import com.example.interfaceui.alias.DeviceAlias
import com.example.interfaceui.broker.BrokerBootstrap
import com.example.interfaceui.data.DevicePrefs
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

class DeviceSelectActivity : AppCompatActivity() {

    private lateinit var spinner: Spinner
    private lateinit var btnAdd: Button
    private lateinit var btnDelete: Button
    private lateinit var btnScan: Button

    private lateinit var adapter: ArrayAdapter<String>

    private var activeScanListener: ((String, String) -> Unit)? = null
    private val mainHandler = Handler(Looper.getMainLooper())

    /** 저장된 ID 목록을 별칭으로 표시 */
    private fun reloadSpinner() {
        val ids = DevicePrefs.getDevices(this)

        val display = if (ids.isEmpty()) {
            listOf(getString(R.string.no_devices))
        } else {
            ids.map { DeviceAlias.labelFor(it) }
        }

        adapter.clear()
        adapter.addAll(display)
        adapter.notifyDataSetChanged()

        val isEmpty = ids.isEmpty()

        spinner.isEnabled = !isEmpty
        btnDelete.isEnabled = !isEmpty
        btnScan.isEnabled = true
        btnAdd.isEnabled = true
        spinner.setSelection(0)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_device_select)

        findViewById<View>(R.id.toolbar)?.let { tb ->
            (tb as? androidx.appcompat.widget.Toolbar)?.setNavigationOnClickListener {
                finish()
            }
        }

        spinner = findViewById(R.id.spinnerDevice)
        btnAdd = findViewById(R.id.btnAdd)
        btnDelete = findViewById(R.id.btnDelete)
        btnScan = findViewById(R.id.btnScan)

        adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            mutableListOf()
        )
        spinner.adapter = adapter

        // 화이트리스트에 없는 기존 항목은 한 번 정리
        val keep = DeviceAlias.allowedIds()

        DevicePrefs.getDevices(this).forEach { id ->
            if (id !in keep) {
                DevicePrefs.removeDevice(this, id)
            }
        }

        reloadSpinner()

        btnAdd.setOnClickListener { showManualAddDialog() }
        btnDelete.setOnClickListener { deleteSelected() }
        btnScan.setOnClickListener { startAutoDiscovery() }
    }

    override fun onStop() {
        activeScanListener?.let { listener ->
            MqttHelper.instance?.removeMessageListener(listener)
        }
        activeScanListener = null
        mainHandler.removeCallbacksAndMessages(null)
        super.onStop()
    }

    private fun showManualAddDialog() {
        val et = EditText(this).apply {
            hint = "예) Neopixel_1"
        }

        AlertDialog.Builder(this)
            .setTitle("기기 추가")
            .setView(et)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("OK") { _, _ ->
                val id = et.text?.toString()?.trim().orEmpty()

                if (id.isNotEmpty()) {
                    if (!DeviceAlias.shouldShow(id)) {
                        Toast.makeText(
                            this,
                            "허용되지 않은 기기(ID: $id)",
                            Toast.LENGTH_SHORT
                        ).show()
                        return@setPositiveButton
                    }

                    DevicePrefs.addDevice(this, id)
                    reloadSpinner()

                    Toast.makeText(
                        this,
                        "${DeviceAlias.labelFor(id)} 추가됨",
                        Toast.LENGTH_SHORT
                    ).show()
                }
            }
            .show()
    }

    private fun deleteSelected() {
        val idx = spinner.selectedItemPosition
        val saved = DevicePrefs.getDevices(this)

        if (idx < 0 || idx >= saved.size) return

        val id = saved[idx]

        DevicePrefs.removeDevice(this, id)
        reloadSpinner()

        Toast.makeText(
            this,
            "${DeviceAlias.labelFor(id)} 삭제됨",
            Toast.LENGTH_SHORT
        ).show()
    }

    // =========================
    // 자동 검색
    // =========================
    private fun startAutoDiscovery() {
        btnScan.isEnabled = false
        Toast.makeText(this, "검색 시작…", Toast.LENGTH_SHORT).show()

        val found = ConcurrentHashMap<String, String>()

        val scanListener: (String, String) -> Unit = listener@{ topic, body ->
            if (!topic.startsWith("interfaceui/registry/hello/")) return@listener

            val idFromTopic = topic
                .substringAfter("interfaceui/registry/hello/")
                .trim()

            val id = try {
                val j = JSONObject(body)
                j.optString(
                    "id",
                    if (idFromTopic.isEmpty()) "unknown" else idFromTopic
                )
            } catch (_: Exception) {
                if (idFromTopic.isEmpty()) "unknown" else idFromTopic
            }

            if (DeviceAlias.shouldShow(id)) {
                val label = DeviceAlias.labelFor(id)
                found[id] = "$label ($id)"
            }
        }

        // 기존 스캔 리스너 제거
        activeScanListener?.let {
            MqttHelper.instance?.removeMessageListener(it)
        }
        activeScanListener = scanListener

        BrokerBootstrap.prepare(applicationContext) { result ->
            runOnUiThread {
                if (!result.connected) {
                    btnScan.isEnabled = true
                    activeScanListener = null

                    Toast.makeText(
                        this,
                        result.errorMessage ?: "MQTT 연결 실패",
                        Toast.LENGTH_SHORT
                    ).show()
                    return@runOnUiThread
                }

                val helper = MqttHelper.instance ?: run {
                    btnScan.isEnabled = true
                    activeScanListener = null
                    return@runOnUiThread
                }

                helper.removeMessageListener(scanListener)
                helper.addMessageListener(scanListener)

                helper.subscribe("interfaceui/registry/hello/#", qos = 1)

                val req = JSONObject(
                    mapOf(
                        "from" to "android",
                        "ts" to System.currentTimeMillis() / 1000
                    )
                ).toString()

                helper.publish(
                    topic = "interfaceui/registry/request",
                    payload = req,
                    qos = 1,
                    retain = false
                )

                mainHandler.postDelayed({
                    helper.removeMessageListener(scanListener)

                    if (activeScanListener === scanListener) {
                        activeScanListener = null
                    }

                    btnScan.isEnabled = true
                    showFoundDialog(found)
                }, 1500L)
            }
        }
    }

    private fun showFoundDialog(found: Map<String, String>) {
        if (found.isEmpty()) {
            Toast.makeText(this, "발견된 기기가 없습니다.", Toast.LENGTH_SHORT).show()
            return
        }

        val entries = found.entries.sortedBy { it.value }
        val items = entries.map { it.value }
        val ids = entries.map { it.key }

        AlertDialog.Builder(this)
            .setTitle("발견된 기기 선택")
            .setItems(items.toTypedArray()) { _, which ->
                val id = ids[which]

                DevicePrefs.addDevice(this, id)
                reloadSpinner()

                Toast.makeText(
                    this,
                    "${DeviceAlias.labelFor(id)} 추가됨",
                    Toast.LENGTH_SHORT
                ).show()
            }
            .setNegativeButton("닫기", null)
            .show()
    }
}
