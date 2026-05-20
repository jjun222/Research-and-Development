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

    private data class Candidate(
        val uri: String,
        val info: BrokerInfo?
    )

    private fun buildCandidateList(
        savedUri: String?,
        discovered: List<BrokerInfo>
    ): List<Candidate> {
        val result = mutableListOf<Candidate>()
        val seen = linkedSetOf<String>()

        if (!savedUri.isNullOrBlank() && seen.add(savedUri)) {
            result.add(Candidate(uri = savedUri, info = null))
        }

        discovered.forEach { info ->
            val uri = info.serverUri
            if (seen.add(uri)) {
                result.add(Candidate(uri = uri, info = info))
            }
        }

        return result
    }

    private fun connectSequentially(
        appContext: Context,
        candidates: List<Candidate>,
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

        val candidate = candidates[index]
        val uri = candidate.uri
        val helper = MqttHelper.switchServer(appContext, uri)

        helper.connect(
            onConnected = {
                BrokerPrefs.saveBrokerInfo(
                    context = appContext,
                    uri = uri,
                    videoUrl = candidate.info?.videoUrl,
                    snapshotUrl = candidate.info?.snapshotUrl,
                    displayName = candidate.info?.displayName
                )

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
            onError = {
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
