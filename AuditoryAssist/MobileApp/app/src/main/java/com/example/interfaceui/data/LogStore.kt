// app/src/main/java/com/example/interfaceui/data/LogStore.kt
package com.example.interfaceui.data

import android.content.ContentValues
import android.content.Context
import android.database.DatabaseUtils
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.util.concurrent.Executors

object LogStore {
    data class Rec(val type: String, val id: String,
                   val level: String, val msg: String, val ts: Long)

    private const val DB_NAME   = "logs.db"
    private const val TBL       = "logs"
    private const val MAX_ROWS  = 50_000
    private val io = Executors.newSingleThreadExecutor()

    private lateinit var helper: LogDbHelper
    fun init(ctx: Context) {
        if (!::helper.isInitialized) helper = LogDbHelper(ctx.applicationContext)
    }

    fun insertAsync(rec: Rec) {
        io.execute {
            val db = helper.writableDatabase
            val cv = ContentValues().apply {
                put("type",  rec.type)
                put("dev_id",rec.id)
                put("level", rec.level)
                put("msg",   rec.msg)
                put("ts",    rec.ts)
            }
            db.insert(TBL, null, cv)
            trim(db)
        }
    }

    private fun trim(db: SQLiteDatabase) {
        val rows = DatabaseUtils.queryNumEntries(db, TBL).toInt()
        if (rows > MAX_ROWS) {
            val over = rows - MAX_ROWS
            db.execSQL(
                "DELETE FROM $TBL WHERE _id IN (" +
                        "SELECT _id FROM $TBL ORDER BY ts ASC, _id ASC LIMIT ?" +
                        ")", arrayOf(over)
            )
        }
    }

    // ---------- ▼ 새로 추가: 최신 n줄 조회 ----------
    fun queryLatestAll(limit: Int): List<Rec> {
        val db = helper.readableDatabase
        val list = mutableListOf<Rec>()
        db.rawQuery(
            "SELECT type,dev_id,level,msg,ts FROM $TBL " +
                    "ORDER BY ts DESC LIMIT ?",
            arrayOf(limit.toString())
        ).use { c ->
            while (c.moveToNext()) {
                list += Rec(c.getString(0), c.getString(1),
                    c.getString(2), c.getString(3), c.getLong(4))
            }
        }
        return list.reversed() // 오래된→최신
    }

    fun queryLatest(type: String, id: String, limit: Int): List<Rec> {
        val db = helper.readableDatabase
        val list = mutableListOf<Rec>()
        db.rawQuery(
            "SELECT type,dev_id,level,msg,ts FROM $TBL " +
                    "WHERE type=? AND dev_id=? " +
                    "ORDER BY ts DESC LIMIT ?",
            arrayOf(type, id, limit.toString())
        ).use { c ->
            while (c.moveToNext()) {
                list += Rec(c.getString(0), c.getString(1),
                    c.getString(2), c.getString(3), c.getLong(4))
            }
        }
        return list.reversed()
    }
    // ---------- ▲ 새로 추가: 최신 n줄 조회 ----------

    fun queryOlderAll(beforeTs: Long, limit: Int): List<Rec> {
        val b = if (beforeTs <= 0) Long.MAX_VALUE else beforeTs
        val db = helper.readableDatabase
        val list = mutableListOf<Rec>()
        db.rawQuery(
            "SELECT type,dev_id,level,msg,ts FROM $TBL " +
                    "WHERE ts < ? ORDER BY ts DESC LIMIT ?",
            arrayOf(b.toString(), limit.toString())
        ).use { c ->
            while (c.moveToNext()) {
                list += Rec(c.getString(0), c.getString(1),
                    c.getString(2), c.getString(3), c.getLong(4))
            }
        }
        return list.reversed()
    }

    fun queryOlder(type: String, id: String, beforeTs: Long, limit: Int): List<Rec> {
        val b = if (beforeTs <= 0) Long.MAX_VALUE else beforeTs
        val db = helper.readableDatabase
        val list = mutableListOf<Rec>()
        db.rawQuery(
            "SELECT type,dev_id,level,msg,ts FROM $TBL " +
                    "WHERE type=? AND dev_id=? AND ts < ? " +
                    "ORDER BY ts DESC LIMIT ?",
            arrayOf(type, id, b.toString(), limit.toString())
        ).use { c ->
            while (c.moveToNext()) {
                list += Rec(c.getString(0), c.getString(1),
                    c.getString(2), c.getString(3), c.getLong(4))
            }
        }
        return list.reversed()
    }

    private class LogDbHelper(ctx: Context)
        : SQLiteOpenHelper(ctx, DB_NAME, null, 1) {
        override fun onCreate(db: SQLiteDatabase) {
            db.execSQL("""
                CREATE TABLE IF NOT EXISTS $TBL(
                  _id    INTEGER PRIMARY KEY AUTOINCREMENT,
                  type   TEXT    NOT NULL,
                  dev_id TEXT    NOT NULL,
                  level  TEXT    NOT NULL,
                  msg    TEXT    NOT NULL,
                  ts     INTEGER NOT NULL
                )
            """.trimIndent())
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_logs_key_ts ON $TBL(type,dev_id,ts)")
            db.execSQL("CREATE INDEX IF NOT EXISTS idx_logs_ts ON $TBL(ts)")
        }
        override fun onUpgrade(db: SQLiteDatabase, oldV: Int, newV: Int) {}
    }
}
