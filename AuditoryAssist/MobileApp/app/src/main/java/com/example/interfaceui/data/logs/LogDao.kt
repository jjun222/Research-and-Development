package com.example.interfaceui.data.logs

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query

@Dao
interface LogDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(e: LogEntity)

    // 최신 N개 (디바이스 필터)
    @Query("""
        SELECT * FROM logs
        WHERE (:device IS NULL OR deviceId = :device)
        ORDER BY ts DESC, id DESC
        LIMIT :limit
    """)
    suspend fun recent(device: String?, limit: Int): List<LogEntity>

    // 더 오래된 페이지네이션 (ts,id 커서)
    @Query("""
        SELECT * FROM logs
        WHERE (:device IS NULL OR deviceId = :device)
          AND (ts < :beforeTs OR (ts = :beforeTs AND id < :beforeId))
        ORDER BY ts DESC, id DESC
        LIMIT :limit
    """)
    suspend fun older(device: String?, beforeTs: Long, beforeId: Long, limit: Int): List<LogEntity>
}
