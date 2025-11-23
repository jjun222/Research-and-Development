package com.example.interfaceui.data.logs

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "logs")
data class LogEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0L,
    val deviceId: String,    // "server" | "Neopixel_1" | …
    val topic: String,
    val level: String?,      // "i","w","e" 등 선택
    val msg: String,
    val ts: Long             // epoch seconds
)
