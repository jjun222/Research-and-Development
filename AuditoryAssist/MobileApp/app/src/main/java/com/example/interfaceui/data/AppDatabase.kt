package com.example.interfaceui.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import com.example.interfaceui.data.db.PushAlertDao
import com.example.interfaceui.data.db.PushAlertEntity

@Database(
    entities = [
        NotificationEntity::class,
        DeviceEntity::class,
        PushAlertEntity::class,       // ★ 추가
    ],
    version = 2,                      // ★ 엔티티 추가됐으니 버전 업
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {

    abstract fun notificationDao(): NotificationDao
    abstract fun deviceDao(): DeviceDao
    abstract fun pushAlertDao(): PushAlertDao   // ★ 추가

    companion object {
        @Volatile private var INSTANCE: AppDatabase? = null

        fun getDatabase(context: Context): AppDatabase =
            INSTANCE ?: synchronized(this) {
                Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "app_database"
                )
                    .setJournalMode(JournalMode.WRITE_AHEAD_LOGGING)
                    .fallbackToDestructiveMigration() // 개발 단계: 스키마 바뀌면 초기화
                    .build()
                    .also { INSTANCE = it }
            }

        // 예전 코드 호환용 별칭
        fun get(context: Context): AppDatabase = getDatabase(context)
    }
}
