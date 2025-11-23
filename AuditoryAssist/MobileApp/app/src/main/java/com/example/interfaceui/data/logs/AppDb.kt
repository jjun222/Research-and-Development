package com.example.interfaceui.data.logs

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(entities = [LogEntity::class], version = 1, exportSchema = false)
abstract class AppDb : RoomDatabase() {
    abstract fun logs(): LogDao

    companion object {
        @Volatile private var inst: AppDb? = null
        fun get(ctx: Context): AppDb =
            inst ?: synchronized(this) {
                inst ?: Room.databaseBuilder(ctx, AppDb::class.java, "logs.db")
                    .fallbackToDestructiveMigration()
                    .build().also { inst = it }
            }
    }
}
