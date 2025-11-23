package com.example.interfaceui.data.db

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "push_alerts")
data class PushAlertEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0L,
    val title: String,
    val body: String,
    /** 수신 시각(UTC millis) */
    val ts: Long
)
