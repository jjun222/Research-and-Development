package com.example.interfaceui.ui1

import android.app.AlertDialog
import android.os.Bundle
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import com.example.interfaceui.MqttHelper
import com.example.interfaceui.R
import com.example.interfaceui.data.DevicePrefs
import com.example.interfaceui.alias.DeviceAlias
import org.json.JSONObject
import java.util.*
import java.util.concurrent.ConcurrentHashMap

class DeviceSelectActivity : AppCompatActivity() {

    private lateinit var spinner: Spinner
    private lateinit var btnAdd: Button
    private lateinit var btnDelete: Button
    private lateinit var btnScan: Button

    private lateinit var adapter: ArrayAdapter<String>

    /** 저장된 ID 목록을 별칭으로 표시 */
    private fun reloadSpinner() {
        val ids = DevicePrefs.getDevices(this)
        val display = if (ids.isEmpty()) listOf(getString(R.string.no_devices))
        else ids.map { DeviceAlias.labelFor(it) }   // ← 컨텍스트 인자 없이 사용

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
            (tb as? androidx.appcompat.widget.Toolbar)?.setNavigationOnClickListener { finish() }
        }

        spinner   = findViewById(R.id.spinnerDevice)
        btnAdd    = findViewById(R.id.btnAdd)
        btnDelete = findViewById(R.id.btnDelete)
        btnScan   = findViewById(R.id.btnScan)

        adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, mutableListOf())
        spinner.adapter = adapter

        // 🔹 화이트리스트에 없는 기존 항목은 한 번 정리
        val keep = DeviceAlias.allowedIds()         // ← 인자 없음
        DevicePrefs.getDevices(this).forEach { id ->
            if (id !in keep) DevicePrefs.removeDevice(this, id)
        }

        reloadSpinner()

        btnAdd.setOnClickListener { showManualAddDialog() }
        btnDelete.setOnClickListener { deleteSelected() }
        btnScan.setOnClickListener { startAutoDiscovery() }
    }

    private fun showManualAddDialog() {
        val et = EditText(this).apply { hint = "예) Neopixel_1" }
        AlertDialog.Builder(this)
            .setTitle("기기 추가")
            .setView(et)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("OK") { _, _ ->
                val id = et.text?.toString()?.trim().orEmpty()
                if (id.isNotEmpty()) {
                    if (!DeviceAlias.shouldShow(id)) {   // ← id만 전달
                        Toast.makeText(this, "허용되지 않은 기기(ID: $id)", Toast.LENGTH_SHORT).show()
                        return@setPositiveButton
                    }
                    DevicePrefs.addDevice(this, id)
                    reloadSpinner()
                    Toast.makeText(this, "${DeviceAlias.labelFor(id)} 추가됨", Toast.LENGTH_SHORT).show()
                }
            }.show()
    }

    private fun deleteSelected() {
        val idx = spinner.selectedItemPosition
        val saved = DevicePrefs.getDevices(this)
        if (idx < 0 || idx >= saved.size) return
        val id = saved[idx]
        DevicePrefs.removeDevice(this, id)
        reloadSpinner()
        Toast.makeText(this, "${DeviceAlias.labelFor(id)} 삭제됨", Toast.LENGTH_SHORT).show()
    }

    // =========================
    // 자동 검색(권장)
    // =========================
    private fun startAutoDiscovery() {
        btnScan.isEnabled = false
        Toast.makeText(this, "검색 시작…", Toast.LENGTH_SHORT).show()

        val found = ConcurrentHashMap<String, String>()

        val scanListener: (String, String) -> Unit = { topic, body ->
            if (topic.startsWith("interfaceui/registry/hello/")) {
                val idFromTopic = topic.substringAfter("interfaceui/registry/hello/").trim()
                val id = try {
                    val j = JSONObject(body)
                    j.optString("id", if (idFromTopic.isEmpty()) "unknown" else idFromTopic)
                } catch (_: Exception) {
                    if (idFromTopic.isEmpty()) "unknown" else idFromTopic
                }

                // 화이트리스트 외 ID는 무시
                if (DeviceAlias.shouldShow(id)) {
                    val label = DeviceAlias.labelFor(id)
                    found[id] = "$label ($id)"
                }
            }
        }

        MqttHelper.connect(
            context = applicationContext,
            onConnected = { ok ->
                runOnUiThread {
                    btnScan.isEnabled = true
                    if (!ok) {
                        Toast.makeText(this, "MQTT 연결 실패", Toast.LENGTH_SHORT).show()
                    } else {
                        // 1) 구독
                        MqttHelper.instance?.subscribe("interfaceui/registry/hello/#", qos = 1)

                        // 2) 기존 리스너 백업 후 스캔 리스너로 교체
                        val old = MqttHelper.instance?.messageListener
                        MqttHelper.instance?.messageListener = scanListener

                        // 3) 재발행 요청
                        val req = JSONObject(mapOf("from" to "android",
                            "ts" to System.currentTimeMillis()/1000)).toString()
                        MqttHelper.instance?.publish("interfaceui/registry/request", req, 1, false)

                        // 4) 1.5초 수집 후 다이얼로그 + 리스너 복구
                        Timer().schedule(object : TimerTask() {
                            override fun run() {
                                runOnUiThread {
                                    if (MqttHelper.instance?.messageListener === scanListener) {
                                        MqttHelper.instance?.messageListener = old
                                    }
                                    showFoundDialog(found)
                                }
                            }
                        }, 1500)
                    }
                }
            },
            onMessage = scanListener // 보험
        )
    }

    private fun showFoundDialog(found: Map<String, String>) {
        if (found.isEmpty()) {
            Toast.makeText(this, "발견된 기기가 없습니다.", Toast.LENGTH_SHORT).show()
            return
        }
        val items = found.values.toList()
        val ids   = found.keys.toList()

        AlertDialog.Builder(this)
            .setTitle("발견된 기기 선택")
            .setItems(items.toTypedArray()) { _, which ->
                val id = ids[which]
                DevicePrefs.addDevice(this, id)
                reloadSpinner()
                Toast.makeText(this, "${DeviceAlias.labelFor(id)} 추가됨", Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton("닫기", null)
            .show()
    }
}
