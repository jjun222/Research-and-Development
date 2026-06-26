package com.example.interfaceui.adapter

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.example.interfaceui.R
import com.example.interfaceui.data.NotificationEntity
import com.example.interfaceui.util.AlertNotificationVisuals
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class NotificationAdapter : RecyclerView.Adapter<NotificationAdapter.VH>() {

    private val items = mutableListOf<NotificationEntity>()

    class VH(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val icon: ImageView = itemView.findViewById(R.id.ivAlertIcon)
        private val title: TextView = itemView.findViewById(R.id.tvTitle)
        private val message: TextView = itemView.findViewById(R.id.tvMessage)
        private val time: TextView = itemView.findViewById(R.id.tvTime)

        private val formatter = SimpleDateFormat(
            "yyyy-MM-dd HH:mm:ss",
            Locale.KOREA
        )

        fun bind(item: NotificationEntity) {
            title.text = item.title
            message.text = item.message
            time.text = formatter.format(Date(item.createdAt))

            val type = AlertNotificationVisuals.resolveType(
                explicitType = null,
                title = item.title,
                body = item.message
            )

            icon.setImageResource(AlertNotificationVisuals.largeIconRes(type))
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_notification, parent, false)
        return VH(view)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size

    fun submit(newItems: List<NotificationEntity>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    fun add(item: NotificationEntity) {
        items.add(0, item)
        notifyItemInserted(0)
    }

    fun clear() {
        val size = items.size
        items.clear()
        if (size > 0) notifyItemRangeRemoved(0, size)
    }
}
