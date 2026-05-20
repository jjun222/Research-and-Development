package com.example.interfaceui.net

data class BrokerInfo(
    val ip: String,
    val port: Int = 1883,
    val name: String? = null,
    val responderIp: String? = null,
    val mqttUri: String? = null,
    val videoUrl: String? = null,
    val snapshotUrl: String? = null
) {
    val serverUri: String
        get() = mqttUri
            ?.takeIf { it.startsWith("tcp://") && it.contains(":") }
            ?: "tcp://$ip:$port"

    val displayName: String
        get() = if (!name.isNullOrBlank()) {
            "$name ($ip:$port)"
        } else {
            "$ip:$port"
        }
}
