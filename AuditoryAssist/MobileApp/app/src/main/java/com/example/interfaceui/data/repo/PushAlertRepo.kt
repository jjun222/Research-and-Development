package com.example.interfaceui.data.repo

import android.content.Context
import com.example.interfaceui.data.AppDatabase
import com.example.interfaceui.data.db.PushAlertEntity
import kotlinx.coroutines.flow.Flow

object PushAlertRepo {
    fun flow(context: Context): Flow<List<PushAlertEntity>> =
        AppDatabase.getDatabase(context).pushAlertDao().observeAll()

    suspend fun add(context: Context, title: String, body: String, ts: Long = System.currentTimeMillis()) {
        AppDatabase.getDatabase(context).pushAlertDao()
            .insert(PushAlertEntity(title = title, body = body, ts = ts))
        // 선택: 너무 쌓이지 않게 관리하고 싶다면 아래 한 줄도 호출
        // AppDatabase.getDatabase(context).pushAlertDao().prune(keep = 200)
    }

    suspend fun clear(context: Context) {
        AppDatabase.getDatabase(context).pushAlertDao().clear()
    }
}
