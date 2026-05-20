package com.example.interfaceui.net

import android.content.Context
import android.util.Log
import com.example.interfaceui.broker.BrokerPrefs
import org.json.JSONObject

object CameraRegistryStore {
    private const val TAG = "CameraRegistryStore"

    fun handleMqttMessage(context: Context, topic: String, payload: String): Boolean {
        val isRegistryTopic = topic.startsWith("interfaceui/registry/hello/")
        val isStatusTopic = topic.startsWith("interfaceui/status/publisher/")

        if (!isRegistryTopic && !isStatusTopic) return false

        val json = try {
            JSONObject(payload)
        } catch (_: Exception) {
            return false
        }

        val id = json.optString("id")
            .ifBlank { json.optString("device_id") }
            .ifBlank { json.optString("sensor_id") }

        val type = json.optString("type")
        val name = json.optString("name").ifBlank { id }

        val videoUrl = json.optString("video_url")
            .ifBlank { json.optString("mjpeg_url") }
            .ifBlank { json.optString("stream_url") }

        val snapshotUrl = json.optString("snapshot_url")
            .ifBlank { json.optString("snapshot") }

        if (videoUrl.isBlank()) return false

        val looksLikeAiCamera =
            id.equals("AI_D_fire", ignoreCase = true) ||
                type.equals("ai_camera", ignoreCase = true) ||
                videoUrl.contains("/video", ignoreCase = true)

        if (!looksLikeAiCamera) return false

        return runCatching {
            BrokerPrefs.saveCameraInfo(
                context = context.applicationContext,
                videoUrl = videoUrl,
                snapshotUrl = snapshotUrl.takeIf { it.isNotBlank() },
                displayName = name.takeIf { it.isNotBlank() }
            )

            Log.d(TAG, "AI camera video URL saved: $videoUrl")
            true
        }.getOrElse { e ->
            Log.w(TAG, "AI camera registry save failed", e)
            false
        }
    }
}
