package com.example.interfaceui.data.db

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface PushAlertDao {

    @Query("SELECT * FROM push_alerts ORDER BY ts DESC")
    fun observeAll(): Flow<List<PushAlertEntity>>

    @Query("SELECT * FROM push_alerts ORDER BY ts DESC")
    suspend fun getAll(): List<PushAlertEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(entity: PushAlertEntity)

    @Query("DELETE FROM push_alerts")
    suspend fun clear()

    /** 너무 쌓이지 않게 최근 N개만 유지하고 나머지 삭제(선택사항) */
    @Query("""
        DELETE FROM push_alerts 
        WHERE id NOT IN (SELECT id FROM push_alerts ORDER BY ts DESC LIMIT :keep)
    """)
    suspend fun prune(keep: Int = 200)
}
