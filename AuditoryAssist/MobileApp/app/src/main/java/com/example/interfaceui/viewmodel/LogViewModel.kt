package com.example.interfaceui.viewmodel

import androidx.lifecycle.ViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

class LogViewModel : ViewModel() {
    private val _logs = MutableStateFlow<Map<String, List<String>>>(emptyMap())
    val logs: StateFlow<Map<String, List<String>>> = _logs

    fun addLog(deviceId: String, log: String) {
        val currentLogs = _logs.value[deviceId].orEmpty()
        val updatedLogs = (currentLogs + log).takeLast(20) // 최신 20줄 유지
        _logs.value = _logs.value + (deviceId to updatedLogs)
    }
}
