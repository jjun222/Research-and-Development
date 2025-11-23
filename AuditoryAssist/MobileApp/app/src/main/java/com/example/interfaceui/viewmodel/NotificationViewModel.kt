package com.example.interfaceui.viewmodel

import androidx.lifecycle.ViewModel
import com.example.interfaceui.data.NotificationDao
import com.example.interfaceui.data.NotificationEntity

class NotificationViewModel(
    private val dao: NotificationDao
) : ViewModel() {

    suspend fun insert(title: String, message: String) {
        dao.insert(NotificationEntity(title = title, message = message))
    }

    suspend fun clearAll() = dao.deleteAll()
}
