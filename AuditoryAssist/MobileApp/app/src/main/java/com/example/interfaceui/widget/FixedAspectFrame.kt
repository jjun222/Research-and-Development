package com.example.interfaceui.widget

import android.content.Context
import android.util.AttributeSet
import android.widget.FrameLayout
import com.example.interfaceui.R
import kotlin.math.max

/**
 * 가로:세로 = ratioW:ratioH 로 정확히 측정되는 컨테이너.
 * 세로 화면에서 가로를 꽉 채우고 높이는 4:3로 맞춰 레터박스(잘림 없음).
 */
class FixedAspectFrame @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : FrameLayout(context, attrs) {

    private var ratioW: Int = 4
    private var ratioH: Int = 3

    init {
        if (attrs != null) {
            val a = context.obtainStyledAttributes(attrs, R.styleable.FixedAspectFrame)
            ratioW = a.getInt(R.styleable.FixedAspectFrame_ratioW, 4)
            ratioH = a.getInt(R.styleable.FixedAspectFrame_ratioH, 3)
            a.recycle()
        }
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val width = MeasureSpec.getSize(widthMeasureSpec)
        val calcHeight = if (ratioW > 0 && ratioH > 0) {
            width * ratioH / ratioW   // width 기준으로 4:3 높이 산출
        } else {
            MeasureSpec.getSize(heightMeasureSpec)
        }
        val hSpec = MeasureSpec.makeMeasureSpec(max(0, calcHeight), MeasureSpec.EXACTLY)
        super.onMeasure(widthMeasureSpec, hSpec)
    }
}
