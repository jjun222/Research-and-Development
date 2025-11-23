package com.example.interfaceui

import android.app.Application
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.ProcessLifecycleOwner
import com.example.interfaceui.data.LogStore
import com.example.interfaceui.data.LogCapture   // ★ 추가

class App : Application() {
    override fun onCreate() {
        super.onCreate()

        // MQTT 기본 연결 초기화(화면용)
        MqttHelper.init(
            context = this,
            serverUri = "tcp://192.168.0.24:1883"
        )

        // 로컬 로그 저장/수집 시작
        LogStore.init(this)
        LogCapture.start(this)   // ★ 상시 수집
        ProcessLifecycleOwner.get().lifecycle.addObserver(
            LifecycleEventObserver { _, event ->
                when (event) {
                    Lifecycle.Event.ON_START -> {}
                    Lifecycle.Event.ON_STOP  -> {}
                    else -> Unit
                }
            }
        )
    }
}
