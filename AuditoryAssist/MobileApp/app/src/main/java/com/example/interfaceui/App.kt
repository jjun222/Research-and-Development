package com.example.interfaceui

import android.app.Application
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.ProcessLifecycleOwner
import com.example.interfaceui.broker.BrokerPrefs
import com.example.interfaceui.data.LogCapture
import com.example.interfaceui.data.LogStore

class App : Application() {
    override fun onCreate() {
        super.onCreate()

        // 저장된 브로커 URI 기준으로 먼저 helper 초기화
        MqttHelper.init(
            context = this,
            serverUri = BrokerPrefs.getBrokerUri(this)
        )

        // 로컬 로그 저장/수집 시작
        LogStore.init(this)
        LogCapture.start(this)

        ProcessLifecycleOwner.get().lifecycle.addObserver(
            LifecycleEventObserver { _, event ->
                when (event) {
                    Lifecycle.Event.ON_START -> {}
                    Lifecycle.Event.ON_STOP -> {}
                    else -> Unit
                }
            }
        )
    }
}
