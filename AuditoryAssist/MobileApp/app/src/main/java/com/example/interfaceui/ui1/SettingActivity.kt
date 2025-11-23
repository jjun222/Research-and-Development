package com.example.interfaceui.ui1

import android.graphics.Color
import android.os.Bundle
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.core.graphics.drawable.DrawableCompat
import androidx.core.widget.doOnTextChanged
import com.example.interfaceui.MqttHelper
import com.example.interfaceui.R
import com.example.interfaceui.data.DevicePrefs
import com.example.interfaceui.alias.DeviceAlias
import com.google.android.material.button.MaterialButton
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.google.android.material.textfield.TextInputEditText
import org.json.JSONObject
import yuku.ambilwarna.AmbilWarnaDialog
import java.util.Locale

class SettingActivity : AppCompatActivity() {

    // UI
    private lateinit var deviceSpinner: Spinner
    private lateinit var btnPickColor: MaterialButton
    private lateinit var btnApply: MaterialButton
    private lateinit var etHex: TextInputEditText
    private lateinit var sbBrightness: SeekBar
    private lateinit var tvBrightnessLabel: View
    private lateinit var previewSwatch: View
    private lateinit var chipGroupPreset: ChipGroup

    // State
    private var currentColor: Int = Color.WHITE
    private var currentBrightness: Int = 255
    private var suppressHexWatcher = false

    private enum class Source { HEX, PICKER, CHIP, RESTORE }
    private val prefs by lazy { getSharedPreferences("setting_prefs", MODE_PRIVATE) }

    /** 스피너 항목(표시는 label, 실제 값은 id) */
    private data class DeviceOption(val id: String?, val label: String) {
        override fun toString(): String = label
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setting)

        deviceSpinner = findViewById(R.id.deviceSpinner)
        btnPickColor = findViewById(R.id.btnPickColor)
        btnApply = findViewById(R.id.btnApply)
        etHex = findViewById(R.id.tvHex)
        sbBrightness = findViewById(R.id.sbBrightness)
        tvBrightnessLabel = findViewById(R.id.tvBrightnessLabel)
        previewSwatch = findViewById(R.id.colorPreview)
        chipGroupPreset = findViewById(R.id.chipGroupPreset)

        populateDeviceSpinner()

        currentColor = prefs.getInt("last_color", Color.WHITE)
        currentBrightness = prefs.getInt("last_brightness", 255)
        sbBrightness.progress = currentBrightness
        setColor(currentColor, Source.RESTORE)

        etHex.doOnTextChanged { text, _, _, _ ->
            if (suppressHexWatcher) return@doOnTextChanged
            val normalized = normalizeHex(text?.toString().orEmpty()) ?: return@doOnTextChanged
            setColor(Color.parseColor("#$normalized"), Source.HEX)
        }

        btnPickColor.setOnClickListener {
            AmbilWarnaDialog(this, currentColor, object : AmbilWarnaDialog.OnAmbilWarnaListener {
                override fun onCancel(dialog: AmbilWarnaDialog) {}
                override fun onOk(dialog: AmbilWarnaDialog, color: Int) { setColor(color, Source.PICKER) }
            }).show()
        }

        for (i in 0 until chipGroupPreset.childCount) {
            val chip = chipGroupPreset.getChildAt(i) as? Chip ?: continue
            chip.setOnClickListener {
                val hex = chip.text?.toString()?.trim()?.removePrefix("#")?.uppercase(Locale.US)
                if (hex != null && hex.matches(Regex("[0-9A-F]{6}"))) {
                    setColor(Color.parseColor("#$hex"), Source.CHIP)
                }
            }
        }

        sbBrightness.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                currentBrightness = progress
            }
            override fun onStartTrackingTouch(seekBar: SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: SeekBar?) { saveBrightness() }
        })

        btnApply.setOnClickListener {
            val target = selectedTargetFromSpinner() // id or null(ALL)
            val ok = publishMood(currentColor, currentBrightness, target)
            Toast.makeText(this, if (ok) "적용되었습니다." else "MQTT 연결이 되지 않았습니다.", Toast.LENGTH_SHORT).show()
        }
    }

    override fun onStart() {
        super.onStart()
        MqttHelper.connect(
            context = applicationContext,
            onConnected = { ok ->
                if (!ok) {
                    runOnUiThread { Toast.makeText(this, "MQTT 연결 실패", Toast.LENGTH_SHORT).show() }
                } else {
                    MqttHelper.instance?.subscribe("devices/+/color/ack", qos = 1)
                    MqttHelper.instance?.subscribe("devices/+/set_mood/ack", qos = 1)
                }
            },
            onMessage = { _, _ -> }
        )
    }

    /** 스피너: 첫 항목은 ALL(=null), 이후 화이트리스트 내 저장 장치만 별칭으로 표시 */
    private fun populateDeviceSpinner() {
        val ids = DevicePrefs.getDevices(this)
            .filter { DeviceAlias.shouldShow(it) }  // ✅ 컨텍스트 인자 제거

        val options = mutableListOf(DeviceOption(null, "ALL"))
        ids.forEach { id -> options += DeviceOption(id, DeviceAlias.resolve(this, id, id)) }

        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, options)
        deviceSpinner.adapter = adapter

        // 저장된 선택 복원
        val selectedId = DevicePrefs.getSelected(this)
        val idx = options.indexOfFirst { it.id == selectedId }
        deviceSpinner.setSelection(if (idx >= 0) idx else 0)

        deviceSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                val pick = options[position]
                DevicePrefs.setSelected(this@SettingActivity, pick.id) // null이면 ALL
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }
    }

    private fun setColor(color: Int, from: Source) {
        currentColor = color
        previewSwatch.background?.let { bg ->
            DrawableCompat.setTint(DrawableCompat.wrap(bg).mutate(), color)
        }
        if (from != Source.HEX) {
            val hex = String.format("#%06X", 0xFFFFFF and color)
            if (!hex.equals(etHex.text?.toString(), ignoreCase = true)) {
                suppressHexWatcher = true
                etHex.setText(hex)
                etHex.setSelection(hex.length)
                suppressHexWatcher = false
            }
        }
        saveColor()
    }

    private fun saveColor() { prefs.edit().putInt("last_color", currentColor).apply() }
    private fun saveBrightness() { prefs.edit().putInt("last_brightness", currentBrightness).apply() }

    private fun publishMood(colorInt: Int, brightness: Int, target: String? = null): Boolean {
        val hex = String.format("#%06X", 0xFFFFFF and colorInt)
        val map = mutableMapOf(
            "command" to "set_mood",
            "color" to hex,
            "brightness" to brightness.coerceIn(0, 255)
        )
        target?.let { map["target"] = it }
        val payload = JSONObject(map as Map<*, *>).toString()

        return MqttHelper.instance?.publish(
            topic = "interfaceui/commands/mood",
            payload = payload,
            qos = 1,
            retain = false
        ) ?: false
    }

    /** 현재 스피너 선택의 ID(null=ALL) */
    private fun selectedTargetFromSpinner(): String? {
        val opt = deviceSpinner.selectedItem as? DeviceOption ?: return null
        return opt.id
    }

    private fun normalizeHex(input: String): String? {
        var s = input.trim().removePrefix("#").uppercase(Locale.US)
        if (s.length == 3) s = "${s[0]}${s[0]}${s[1]}${s[1]}${s[2]}${s[2]}"
        if (s.length != 6) return null
        if (!s.matches(Regex("[0-9A-F]{6}"))) return null
        return s
    }
}
