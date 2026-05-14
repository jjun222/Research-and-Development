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

        // 저장된 MQTT Broker URI가 있을 때만 MqttHelper를 초기화한다.
        // 첫 실행 또는 브로커 미등록 상태에서는 MainActivity / BrokerBootstrap에서 연결을 처리한다.
        val savedBrokerUri = BrokerPrefs.getBrokerUriOrNull(this)

        if (!savedBrokerUri.isNullOrBlank()) {
            MqttHelper.init(
                context = this,
                serverUri = savedBrokerUri
            )
        }

        // 로컬 로그 저장/수집 시작
        LogStore.init(this)
        LogCapture.start(this)

        ProcessLifecycleOwner.get().lifecycle.addObserver(
            LifecycleEventObserver { _, event ->
                when (event) {
                    Lifecycle.Event.ON_START -> {
                        // 필요 시 앱 foreground 상태 처리
                    }

                    Lifecycle.Event.ON_STOP -> {
                        // 필요 시 앱 background 상태 처리
                    }

                    else -> Unit
                }
            }
        )
    }
}
