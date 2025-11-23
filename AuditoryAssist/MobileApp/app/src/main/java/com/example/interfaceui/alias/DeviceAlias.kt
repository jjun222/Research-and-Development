package com.example.interfaceui.alias

import android.content.Context
import android.content.SharedPreferences
import java.util.Locale

object DeviceAlias {
    private const val PREF = "device_alias"

    // 기본 별칭 (필요 시 여기만 수정하면 앱 전체 표시명이 바뀝니다)
    private val defaults = mapOf(
        "server"     to "중앙 관리 서버",
        "Neopixel_1" to "거실 무드등",
        "Neopixel_2" to "안방 무드등",
        // 필요 없으면 3/4는 안 넣습니다
    )

    // ID 표준화(대/소문자, 하이픈/언더스코어 등 혼용 정리)
    private val normalizers = mapOf(
        "server"      to "server",
        "neopixel_1"  to "Neopixel_1",
        "neopixel_2"  to "Neopixel_2",
        // "neopixel-1" 처럼 들어와도 아래 규칙으로 처리됨
    )

    /** 토픽/페이로드에서 온 원시 ID를 앱에서 쓰는 표준 ID로 통일 */
    fun canonicalId(raw: String?): String {
        val s = raw?.trim().orEmpty()
        val key = s.lowercase(Locale.getDefault())
        // 하이픈을 언더스코어로 교정 후 조회
        val fixed = key.replace('-', '_')
        return normalizers[fixed] ?: s
    }

    /** 허용(표시)할 기기 목록 */
    fun allowedIds(): Set<String> = setOf("server", "Neopixel_1", "Neopixel_2")

    /** 목록/검색 결과에 노출해도 되는가 */
    fun shouldShow(id: String): Boolean = allowedIds().contains(canonicalId(id))

    /** 컨텍스트 없이 “기본 별칭 또는 ID”를 돌려줌(간단 표시에 사용) */
    fun labelFor(id: String): String {
        val cid = canonicalId(id)
        return defaults[cid] ?: cid
    }

    private fun prefs(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE)

    /** 실제 화면 표기명: 저장된 사용자 별칭 → 기본 별칭 → 원래 이름 → ID */
    fun resolve(ctx: Context, id: String, originalName: String?): String {
        val cid = canonicalId(id)
        val user = prefs(ctx).getString(cid, null)
        return user ?: defaults[cid] ?: originalName ?: cid
    }

    /** 사용자 별칭 저장/삭제(빈 문자열이면 삭제) */
    fun set(ctx: Context, id: String, alias: String?) {
        val cid = canonicalId(id)
        prefs(ctx).edit().apply {
            if (alias.isNullOrBlank()) remove(cid) else putString(cid, alias)
        }.apply()
    }
}
