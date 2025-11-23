package com.example.interfaceui.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query

@Dao
interface NotificationDao {
    @Query("SELECT * FROM notifications ORDER BY id DESC")
    suspend fun getAll(): List<NotificationEntity>
    @Insert suspend fun insert(entity: NotificationEntity)
    @Query("DELETE FROM notifications") suspend fun deleteAll()
}
