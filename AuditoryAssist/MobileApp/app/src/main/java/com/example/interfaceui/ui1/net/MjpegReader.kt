package com.example.interfaceui.net

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import kotlinx.coroutines.isActive
import kotlinx.coroutines.yield
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.ByteArrayOutputStream
import java.io.InputStream
import kotlin.coroutines.cancellation.CancellationException

class MjpegReader(
    private val client: OkHttpClient = OkHttpClient.Builder()
        .retryOnConnectionFailure(true)
        .build()
) {

    /**
     * MJPEG(multipart/x-mixed-replace) 스트림을 읽어 JPEG 프레임을 콜백으로 전달.
     * cancelRequested() 가 true를 반환하면 루프 종료.
     */
    @Throws(Exception::class)
    suspend fun read(
        url: String,
        onFrame: (Bitmap) -> Unit,
        onConnected: () -> Unit = {},
        onDisconnected: (Throwable?) -> Unit = {},
        cancelRequested: suspend () -> Boolean
    ) {
        val req = Request.Builder()
            .url(url)
            .header("User-Agent", "Android-MJPEG")
            .build()

        val call = client.newCall(req)
        val resp = call.execute()
        if (!resp.isSuccessful) {
            resp.close()
            throw IllegalStateException("HTTP ${resp.code}")
        }
        onConnected()

        val `is`: InputStream = resp.body!!.byteStream()
        var prev = -1
        var collecting = false
        val baos = ByteArrayOutputStream()
        val buf = ByteArray(8 * 1024)

        try {
            while (true) {
                if (cancelRequested()) break
                val read = `is`.read(buf)
                if (read == -1) break
                for (i in 0 until read) {
                    val b = buf[i].toInt() and 0xFF
                    if (!collecting) {
                        if (prev == 0xFF && b == 0xD8) {
                            collecting = true
                            baos.reset()
                            baos.write(0xFF)
                            baos.write(0xD8)
                        }
                    } else {
                        baos.write(b)
                        if (prev == 0xFF && b == 0xD9) {
                            // 한 프레임 완성
                            val data = baos.toByteArray()
                            BitmapFactory.decodeByteArray(data, 0, data.size)?.let { bmp ->
                                onFrame(bmp)
                            }
                            collecting = false
                        }
                    }
                    prev = b
                }
                // 협조적 취소
                yield()
            }
        } catch (ce: CancellationException) {
            onDisconnected(null)
            throw ce
        } catch (t: Throwable) {
            onDisconnected(t)
            throw t
        } finally {
            try { `is`.close() } catch (_: Exception) {}
            resp.close()
        }
    }
}
