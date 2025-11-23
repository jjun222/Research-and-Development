package com.example.interfaceui.ui1

import android.annotation.SuppressLint
import android.os.Bundle
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import com.example.interfaceui.R
import com.example.interfaceui.ui1.common.setupUpToolbar

class WebViewActivity : AppCompatActivity() {

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_webview)

        setupUpToolbar(R.id.toolbar, getString(R.string.camera_title))

        val url = intent.getStringExtra(EXTRA_URL) ?: "http://192.168.0.57:5000/video"

        val wv = findViewById<WebView>(R.id.webView)
        wv.settings.javaScriptEnabled = true
        wv.settings.mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
        wv.webViewClient = object : WebViewClient() {}
        wv.webChromeClient = WebChromeClient()
        wv.loadUrl(url)
    }

    companion object {
        const val EXTRA_URL = "extra_url"
    }
}
