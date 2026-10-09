package app.arienx.arienx_mobile

import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import io.flutter.plugin.common.EventChannel
import java.io.File

class MainActivity: FlutterActivity() {
    private var bridge: MethodChannel? = null
    private var liveAudio: LiveAudio? = null
    override fun configureFlutterEngine(engine: FlutterEngine) {
        super.configureFlutterEngine(engine)
        val audio = LiveAudio(this)
        liveAudio = audio
        MethodChannel(engine.dartExecutor.binaryMessenger, "app.arienx/live-audio").setMethodCallHandler { call, result -> audio.handle(call,result) }
        EventChannel(engine.dartExecutor.binaryMessenger, "app.arienx/live-audio/events").setStreamHandler(audio)
        val updates = AppUpdates(this)
        AppUpdates.schedule(this)
        MethodChannel(engine.dartExecutor.binaryMessenger, "app.arienx/updates").setMethodCallHandler(updates::handle)
        bridge = MethodChannel(engine.dartExecutor.binaryMessenger, "app.arienx/share")
        bridge?.setMethodCallHandler { call, result ->
            if (call.method == "takeShared") result.success(takeShared()) else result.notImplemented()
        }
    }
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        if(liveAudio?.permissionResult(requestCode,grantResults) != true) super.onRequestPermissionsResult(requestCode,permissions,grantResults)
    }
    override fun onStart() { super.onStart(); AppUpdates.foreground = true }
    override fun onStop() { AppUpdates.foreground = false; super.onStop() }
    override fun onPause() { liveAudio?.stop(); super.onPause() }
    override fun onDestroy() { liveAudio?.stop(); super.onDestroy() }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent.action == Intent.ACTION_SEND) bridge?.invokeMethod("shared", null)
    }
    @Suppress("DEPRECATION")
    private fun takeShared(): Map<String, String>? {
        val incoming = intent ?: return null
        if (incoming.action != Intent.ACTION_SEND) return null
        incoming.action = Intent.ACTION_MAIN
        return try {
            val uri = incoming.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
            val target = File(cacheDir, "arienx-shared-${System.currentTimeMillis()}")
            var name = "shared-text.txt"
            if (uri != null && uri.scheme == "content") {
                contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                    if (c.moveToFirst()) name = c.getString(0) ?: "shared-file"
                }
                contentResolver.openInputStream(uri)?.use { input ->
                    target.outputStream().use { output ->
                        val buffer = ByteArray(8192); var total = 0
                        while (true) { val count = input.read(buffer); if (count < 0) break; total += count; require(total <= 3 * 1024 * 1024); output.write(buffer, 0, count) }
                    }
                } ?: return null
            } else {
                val text = incoming.getStringExtra(Intent.EXTRA_TEXT) ?: return null
                require(text.length <= 500000)
                target.writeText(text)
            }
            mapOf("path" to target.path, "name" to name)
        } catch (_: Exception) { mapOf("error" to "Shared item is unavailable or exceeds 3 MB.") }
    }
}
