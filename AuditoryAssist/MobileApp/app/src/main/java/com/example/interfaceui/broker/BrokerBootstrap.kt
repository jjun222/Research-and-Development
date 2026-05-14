package com.example.interfaceui.broker

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.example.interfaceui.MqttHelper
import com.example.interfaceui.net.BrokerDiscovery
import com.example.interfaceui.net.BrokerInfo
import com.example.interfaceui.service.PushTokenRegistrar

data class BrokerConnectResult(
    val uri: String?,
    val connected: Boolean,
    val discovered: Boolean,
    val errorMessage: String? = null
)

object BrokerBootstrap {

    fun prepare(
        context: Context,
        onResult: (BrokerConnectResult) -> Unit = {}
    ) {
        val appContext = context.applicationContext
        val savedUri = BrokerPrefs.getBrokerUriOrNull(appContext)

        BrokerDiscovery.discoverAll(timeoutMs = 1500) { discovered ->
            val candidates = buildCandidateList(savedUri, discovered)

            Handler(Looper.getMainLooper()).post {
                connectSequentially(
                    appContext = appContext,
                    candidates = candidates,
                    index = 0,
                    discoveredUris = discovered.map { it.serverUri }.toSet(),
                    onResult = onResult
                )
            }
        }
    }

    private fun buildCandidateList(
        savedUri: String?,
        discovered: List<BrokerInfo>
    ): List<String> {
        val result = mutableListOf<String>()

        if (!savedUri.isNullOrBlank()) {
            result.add(savedUri)
        }

        discovered.forEach { info ->
            if (info.serverUri !in result) {
                result.add(info.serverUri)
            }
        }

        return result
    }

    private fun connectSequentially(
        appContext: Context,
        candidates: List<String>,
        index: Int,
        discoveredUris: Set<String>,
        onResult: (BrokerConnectResult) -> Unit
    ) {
        if (candidates.isEmpty()) {
            onResult(
                BrokerConnectResult(
                    uri = null,
                    connected = false,
                    discovered = false,
                    errorMessage = "저장된 브로커도 없고, 자동 검색된 브로커도 없습니다."
                )
            )
            return
        }

        if (index >= candidates.size) {
            onResult(
                BrokerConnectResult(
                    uri = null,
                    connected = false,
                    discovered = false,
                    errorMessage = "모든 MQTT Broker 연결 시도가 실패했습니다."
                )
            )
            return
        }

        val uri = candidates[index]
        val helper = MqttHelper.switchServer(appContext, uri)

        helper.connect(
            onConnected = {
                BrokerPrefs.saveBrokerUri(appContext, uri)

                PushTokenRegistrar.flushPendingToken(appContext)

                onResult(
                    BrokerConnectResult(
                        uri = uri,
                        connected = true,
                        discovered = uri in discoveredUris,
                        errorMessage = null
                    )
                )
            },
            onError = { e ->
                connectSequentially(
                    appContext = appContext,
                    candidates = candidates,
                    index = index + 1,
                    discoveredUris = discoveredUris,
                    onResult = onResult
                )
            }
        )
    }
}
