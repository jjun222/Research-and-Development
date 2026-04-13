package com.example.interfaceui.broker

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.example.interfaceui.MqttHelper
import com.example.interfaceui.net.BrokerDiscovery
import com.example.interfaceui.net.BrokerInfo

object BrokerBootstrap {

    fun prepare(
        context: Context,
        onReady: (String) -> Unit = {}
    ) {
        val appContext = context.applicationContext
        val savedUri = BrokerPrefs.getBrokerUri(appContext)

        // 저장된 브로커 URI 기준으로 먼저 helper 준비
        MqttHelper.init(appContext, savedUri)

        BrokerDiscovery.discoverAll(timeoutMs = 1500) { discovered ->
            val candidateUri = pickBrokerUri(savedUri, discovered)

            Handler(Looper.getMainLooper()).post {
                connectWithFallback(
                    appContext = appContext,
                    primaryUri = candidateUri,
                    fallbackUri = savedUri,
                    onReady = onReady
                )
            }
        }
    }

    private fun pickBrokerUri(
        savedUri: String,
        discovered: List<BrokerInfo>
    ): String {
        if (discovered.isEmpty()) return savedUri

        discovered.firstOrNull { it.serverUri == savedUri }?.let {
            return it.serverUri
        }

        // 1차 목표: 자동 발견되면 첫 번째 브로커로 자동 연결
        return discovered.first().serverUri
    }

    private fun connectWithFallback(
        appContext: Context,
        primaryUri: String,
        fallbackUri: String,
        onReady: (String) -> Unit
    ) {
        val primary = MqttHelper.switchServer(appContext, primaryUri)
        primary.connect(
            onConnected = {
                BrokerPrefs.saveBrokerUri(appContext, primaryUri)
                onReady(primaryUri)
            },
            onError = {
                if (primaryUri == fallbackUri) {
                    onReady(fallbackUri)
                    return@connect
                }

                val fallback = MqttHelper.switchServer(appContext, fallbackUri)
                fallback.connect(
                    onConnected = {
                        BrokerPrefs.saveBrokerUri(appContext, fallbackUri)
                        onReady(fallbackUri)
                    },
                    onError = {
                        onReady(fallbackUri)
                    }
                )
            }
        )
    }
}
