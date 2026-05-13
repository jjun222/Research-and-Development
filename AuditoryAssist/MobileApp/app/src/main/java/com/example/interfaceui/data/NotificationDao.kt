package com.example.interfaceui.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface NotificationDao {

    @Query("SELECT * FROM notifications ORDER BY createdAt DESC, id DESC")
    fun observeAll(): Flow<List<NotificationEntity>>

    @Query("SELECT * FROM notifications ORDER BY createdAt DESC, id DESC")
    suspend fun getAll(): List<NotificationEntity>

    @Insert
    suspend fun insert(entity: NotificationEntity): Long

    @Query("DELETE FROM notifications")
    suspend fun deleteAll()
}
