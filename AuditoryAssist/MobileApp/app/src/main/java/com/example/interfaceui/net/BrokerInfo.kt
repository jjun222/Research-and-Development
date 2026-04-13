package com.example.interfaceui.net

data class BrokerInfo(
    val ip: String,
    val port: Int = 1883,
    val name: String? = null,
    val responderIp: String? = null
) {
    val serverUri: String
        get() = "tcp://$ip:$port"

    val displayName: String
        get() = if (!name.isNullOrBlank()) {
            "$name ($ip:$port)"
        } else {
            "$ip:$port"
        }
}
