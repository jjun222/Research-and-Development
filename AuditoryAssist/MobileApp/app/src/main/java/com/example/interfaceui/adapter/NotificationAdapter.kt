package com.example.interfaceui.adapter

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.example.interfaceui.R
import com.example.interfaceui.data.NotificationEntity

/**
 * 단순 RecyclerView.Adapter 구현 (데이터 바인딩 X)
 */
class NotificationAdapter : RecyclerView.Adapter<NotificationAdapter.VH>() {

    private val items = mutableListOf<NotificationEntity>()

    class VH(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val title: TextView = itemView.findViewById(R.id.tvTitle)
        private val message: TextView = itemView.findViewById(R.id.tvMessage)

        fun bind(item: NotificationEntity) {
            title.text = item.title
            message.text = item.message
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

    /** 목록 통째로 교체 */
    fun submit(newItems: List<NotificationEntity>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    /** 단일 추가(상단에 삽입) */
    fun add(item: NotificationEntity) {
        items.add(0, item)
        notifyItemInserted(0)
    }

    /** 전체 삭제 */
    fun clear() {
        val size = items.size
        items.clear()
        if (size > 0) notifyItemRangeRemoved(0, size)
    }
}
