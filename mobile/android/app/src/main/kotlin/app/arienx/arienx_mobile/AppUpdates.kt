package app.arienx.arienx_mobile

import android.app.*
import android.app.job.*
import android.content.*
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.*
import android.os.Build
import android.provider.Settings
import io.flutter.embedding.android.FlutterActivity
import io.flutter.plugin.common.MethodChannel
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicBoolean

// Reuse the official release feed and certificate checks; Android owns installation.
class AppUpdates(private val activity: FlutterActivity) {
    fun handle(call: io.flutter.plugin.common.MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "configureAutoUpdates" -> {
                preferences(activity).edit().putBoolean("enabled", call.arguments != false).apply()
                schedule(activity)
                result.success(status(activity))
            }
            "updateStatus" -> result.success(status(activity))
            "allowUpdates" -> {
                if (!allowed(activity)) activity.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${activity.packageName}")))
                result.success(null)
            }
            "approveUpdate" -> {
                val pending = preferences(activity).getString("approval", null)
                if (pending != null) {
                    val original = Intent.parseUri(pending, Intent.URI_INTENT_SCHEME)
                    val action = PendingIntent.getActivity(activity, NOTICE, original, PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE)
                    if (action != null) action.send() else result.error("installer", "The approval expired. Tap Check updates to retry.", null)
                    if (action == null) { preferences(activity).edit().remove("approval").remove("session").apply(); return }
                }
                result.success(null)
            }
            "autoCheckUpdates" -> {
                schedule(activity)
                if (!preferences(activity).getBoolean("enabled", true) || !wifi(activity)) { result.success(null); return }
                Thread {
                    try {
                        checkAutomatic(activity.applicationContext, AtomicBoolean(false), false)
                        activity.runOnUiThread { result.success(null) }
                    } catch (_: Exception) { activity.runOnUiThread { result.success(null) } }
                }.start()
            }
            "installUpdate" -> {
                if (!allowed(activity)) {
                    activity.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${activity.packageName}")))
                    result.error("permission", "Allow updates from ArienX once, return, then tap Check updates.", null)
                    return
                }
                Thread {
                    try {
                        check(busy.compareAndSet(false, true)) { "An update is already being prepared." }
                        try {
                            val release = Release(call.argument<String>("url") ?: "", call.argument<String>("sha256") ?: "", (call.argument<Number>("versionCode") ?: 0).toLong())
                            val file = download(activity, release, AtomicBoolean(false), false)
                            commit(activity, file, release.code, true)
                            activity.runOnUiThread { result.success("Update sent to Android. It installs automatically where permitted; otherwise approve Android's prompt.") }
                        } finally { busy.set(false) }
                    } catch (e: Exception) { activity.runOnUiThread { result.error("update", e.message ?: "Update failed", null) } }
                }.start()
            }
            else -> result.notImplemented()
        }
    }

    companion object {
        private const val PERIODIC = 8401
        private const val IDLE = 8402
        private const val NOTICE = 8403
        private const val FEED = "https://myarienx.netlify.app/download/latest.json"
        private val busy = AtomicBoolean(false)
        @Volatile var foreground = false
        private fun preferences(c: Context) = c.getSharedPreferences("arienx-updates", Context.MODE_PRIVATE)
        private fun allowed(c: Context) = Build.VERSION.SDK_INT < 26 || c.packageManager.canRequestPackageInstalls()
        private fun wifi(c: Context): Boolean {
            val cm = c.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val network = cm.getNetworkCapabilities(cm.activeNetwork)
            return network?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true && network.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
        }
        @Suppress("DEPRECATION")
        private fun version(c: Context): Long {
            val installed = c.packageManager.getPackageInfo(c.packageName, 0)
            return if (Build.VERSION.SDK_INT >= 28) installed.longVersionCode else installed.versionCode.toLong()
        }
        fun status(c: Context): Map<String, Any> {
            cleanup(c)
            val p = preferences(c)
            return mapOf("enabled" to p.getBoolean("enabled", true), "installAllowed" to allowed(c),
                "approvalRequired" to (p.getString("approval", null) != null),
                "message" to (p.getString("message", null) ?: "Updates download automatically on Wi-Fi and install while your phone is idle."))
        }
        private fun cleanup(c: Context) {
            val p = preferences(c)
            val pendingCode = p.getLong("pendingCode", 0)
            if (pendingCode > 0 && pendingCode <= version(c)) {
                p.edit().remove("approval").remove("session").remove("pendingCode").putString("message", "ArienX is up to date.").apply()
                File(c.cacheDir, "updates").deleteRecursively()
                (c.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(NOTICE)
            }
            val session = p.getInt("session", -1)
            if (session >= 0 && c.packageManager.packageInstaller.getSessionInfo(session) == null) {
                p.edit().remove("session").remove("approval").apply()
            }
        }
        fun schedule(c: Context) {
            cleanup(c)
            val jobs = c.getSystemService(Context.JOB_SCHEDULER_SERVICE) as JobScheduler
            if (!preferences(c).getBoolean("enabled", true)) {
                jobs.cancel(PERIODIC); jobs.cancel(IDLE)
                return
            }
            if (jobs.getPendingJob(PERIODIC) == null) {
                jobs.schedule(JobInfo.Builder(PERIODIC, ComponentName(c, UpdateJob::class.java))
                    .setRequiredNetworkType(JobInfo.NETWORK_TYPE_UNMETERED).setRequiresDeviceIdle(true)
                    .setPersisted(true).setPeriodic(12 * 60 * 60 * 1000L).build())
            }
        }
        private fun idleSoon(c: Context) {
            if (!preferences(c).getBoolean("enabled", true)) return
            val jobs = c.getSystemService(Context.JOB_SCHEDULER_SERVICE) as JobScheduler
            jobs.schedule(JobInfo.Builder(IDLE, ComponentName(c, UpdateJob::class.java))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_UNMETERED).setRequiresDeviceIdle(true).build())
        }
        private data class Release(val url: String, val hash: String, val code: Long) {
            init {
                val uri = Uri.parse(url)
                require(uri.scheme == "https" && uri.host == "myarienx.netlify.app" &&
                    (uri.port == -1 || uri.port == 443) && uri.userInfo == null && uri.query == null && uri.fragment == null &&
                    uri.path?.startsWith("/download/") == true && uri.path?.endsWith(".apk") == true) { "Invalid official update URL." }
                require(hash.matches(Regex("[a-f0-9]{64}")) && code > 0) { "Invalid release metadata." }
            }
        }
        private fun connection(url: String): HttpURLConnection = (URL(url).openConnection() as HttpURLConnection).apply {
            instanceFollowRedirects = false; connectTimeout = 15000; readTimeout = 30000
            setRequestProperty("Cache-Control", "no-cache")
        }
        private fun latest(): Release {
            val connection = connection(FEED)
            try {
                check(connection.responseCode == 200) { "Update service unavailable." }
                val bytes = connection.inputStream.use { input ->
                    val output = java.io.ByteArrayOutputStream(); val buffer = ByteArray(4096)
                    while (output.size() <= 32768) { val n = input.read(buffer); if (n < 0) break; output.write(buffer, 0, n) }
                    output.toByteArray()
                }
                require(bytes.size <= 32768) { "Invalid release metadata." }
                val data = JSONObject(String(bytes, Charsets.UTF_8))
                return Release(data.getString("url"), data.getString("sha256"), data.getLong("versionCode"))
            } finally { connection.disconnect() }
        }
        private fun continueWork(c: Context, cancelled: AtomicBoolean, automatic: Boolean) {
            check(!cancelled.get() && (!automatic || preferences(c).getBoolean("enabled", true))) { "Update preparation stopped." }
        }
        private fun digest(file: File): String {
            val sha = MessageDigest.getInstance("SHA-256")
            file.inputStream().use { input -> val b = ByteArray(65536); while (true) { val n = input.read(b); if (n < 0) break; sha.update(b, 0, n) } }
            return sha.digest().joinToString("") { "%02x".format(it) }
        }
        @Suppress("DEPRECATION")
        private fun download(c: Context, release: Release, cancelled: AtomicBoolean, automatic: Boolean): File {
            continueWork(c, cancelled, automatic)
            val folder = File(c.cacheDir, "updates").apply { mkdirs() }
            val file = File(folder, "ArienX-${release.code}.apk")
            if (!file.exists() || digest(file) != release.hash) {
                val part = File(folder, "ArienX-${release.code}.part")
                val connection = connection(release.url)
                try {
                    check(connection.responseCode == 200) { "Official download failed." }
                    connection.inputStream.use { input -> part.outputStream().use { output ->
                        val buffer = ByteArray(65536); var total = 0L
                        while (true) {
                            continueWork(c, cancelled, automatic)
                            val n = input.read(buffer); if (n < 0) break
                            total += n; require(total <= 250L * 1024 * 1024) { "APK too large." }
                            output.write(buffer, 0, n)
                        }
                    } }
                    require(digest(part) == release.hash) { "APK checksum mismatch." }
                    if (file.exists()) file.delete()
                    check(part.renameTo(file)) { "Could not save update." }
                } finally { connection.disconnect(); part.delete() }
            }
            val pm = c.packageManager
            val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
            val own = pm.getPackageInfo(c.packageName, flags)
            val archive = pm.getPackageArchiveInfo(file.path, flags) ?: error("Invalid APK.")
            require(archive.packageName == c.packageName) { "Wrong app package." }
            val code = if (Build.VERSION.SDK_INT >= 28) archive.longVersionCode else archive.versionCode.toLong()
            require(code == release.code && code > version(c)) { "Update version is not newer." }
            val ownSigners = if (Build.VERSION.SDK_INT >= 28) own.signingInfo?.apkContentsSigners else own.signatures
            val incoming = if (Build.VERSION.SDK_INT >= 28) archive.signingInfo?.apkContentsSigners else archive.signatures
            require(!ownSigners.isNullOrEmpty() && !incoming.isNullOrEmpty() &&
                ownSigners.map { it.toCharsString() }.toSet() == incoming.map { it.toCharsString() }.toSet()) { "APK signing certificate mismatch." }
            return file
        }
        fun checkAutomatic(c: Context, cancelled: AtomicBoolean, idle: Boolean) {
            if (!preferences(c).getBoolean("enabled", true) || !wifi(c) || !busy.compareAndSet(false, true)) return
            try {
                cleanup(c)
                if (preferences(c).getInt("session", -1) >= 0) return
                continueWork(c, cancelled, true)
                val release = latest()
                if (release.code <= version(c)) {
                    preferences(c).edit().putString("message", "ArienX is up to date.").apply(); return
                }
                if (!allowed(c)) {
                    preferences(c).edit().putString("message", "Allow updates from ArienX once to enable automatic installation.").apply()
                    notice(c, "Enable ArienX updates", "Allow app updates once in ArienX Settings.", Intent(c, MainActivity::class.java))
                    return
                }
                preferences(c).edit().putString("message", "Downloading an official update automatically…").apply()
                val file = download(c, release, cancelled, true)
                continueWork(c, cancelled, true)
                if (idle && !foreground) commit(c, file, release.code, false, cancelled)
                else {
                    preferences(c).edit().putString("message", "Update downloaded and verified. It will install while the phone is idle.").apply()
                    idleSoon(c)
                }
            } finally { busy.set(false) }
        }
        private fun commit(c: Context, file: File, code: Long, interactive: Boolean, cancelled: AtomicBoolean = AtomicBoolean(false)) {
            cleanup(c)
            if (preferences(c).getInt("session", -1) >= 0) return
            continueWork(c, cancelled, !interactive)
            val installer = c.packageManager.packageInstaller
            val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
                setAppPackageName(c.packageName); setSize(file.length())
                if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
                if (Build.VERSION.SDK_INT >= 33) setPackageSource(PackageInstaller.PACKAGE_SOURCE_DOWNLOADED_FILE)
            }
            val sessionId = installer.createSession(params)
            try {
                installer.openSession(sessionId).use { session ->
                    session.openWrite("base.apk", 0, file.length()).use { output ->
                        file.inputStream().use { input ->
                            val buffer = ByteArray(65536)
                            while (true) { continueWork(c, cancelled, !interactive); val n = input.read(buffer); if (n < 0) break; output.write(buffer, 0, n) }
                        }
                        session.fsync(output)
                    }
                    continueWork(c, cancelled, !interactive)
                    if (!interactive && foreground) { installer.abandonSession(sessionId); idleSoon(c); return }
                    val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
                    val callback = PendingIntent.getBroadcast(c, sessionId, Intent(c, UpdateInstallReceiver::class.java).setAction("${c.packageName}.UPDATE_RESULT"), flags)
                    preferences(c).edit().putInt("session", sessionId).putLong("pendingCode", code).putBoolean("interactive", interactive)
                        .putString("message", "Android is applying the verified update…").commit()
                    session.commit(callback.intentSender)
                }
            } catch (e: Exception) {
                installer.abandonSession(sessionId)
                preferences(c).edit().remove("session").remove("approval").apply()
                throw e
            }
        }
        @Suppress("DEPRECATION")
        fun installed(c: Context, intent: Intent) {
            val p = preferences(c)
            if (intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1) != p.getInt("session", -2)) return
            when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
                PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                    val approval = if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java) else intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                    if (approval != null) {
                        p.edit().putString("approval", approval.toUri(Intent.URI_INTENT_SCHEME)).putString("message", "Android requires your approval. Tap Finish update; no website download is needed.").commit()
                        if (foreground && p.getBoolean("interactive", false)) {
                            try { c.startActivity(approval.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } catch (_: Exception) { notice(c, "Finish ArienX update", "Tap to approve Android's installation request.", approval) }
                        } else notice(c, "Finish ArienX update", "Tap to approve Android's installation request.", approval)
                    }
                }
                PackageInstaller.STATUS_SUCCESS -> {
                    p.edit().remove("session").remove("approval").putString("message", "ArienX updated successfully.").apply()
                    cleanup(c); schedule(c)
                }
                else -> p.edit().remove("session").remove("approval").putString("message", "Android did not install the update. Tap Check updates to retry.").apply()
            }
        }
        private fun notice(c: Context, title: String, message: String, intent: Intent) {
            val pending = PendingIntent.getActivity(c, NOTICE, intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            if (Build.VERSION.SDK_INT >= 33 && c.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
            val notifications = c.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (Build.VERSION.SDK_INT >= 26) notifications.createNotificationChannel(NotificationChannel("arienx_updates", "App updates", NotificationManager.IMPORTANCE_DEFAULT))
            val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(c, "arienx_updates") else Notification.Builder(c)
            notifications.notify(NOTICE, builder.setSmallIcon(android.R.drawable.stat_sys_download_done).setContentTitle(title).setContentText(message).setContentIntent(pending).setAutoCancel(true).build())
        }
    }
}

class UpdateJob: JobService() {
    private val cancellations = java.util.concurrent.ConcurrentHashMap<Int, AtomicBoolean>()
    override fun onStartJob(params: JobParameters): Boolean {
        val cancellation = AtomicBoolean(false); cancellations[params.jobId] = cancellation
        Thread {
            var retry = false
            try { AppUpdates.checkAutomatic(applicationContext, cancellation, true) } catch (_: Exception) { retry = !cancellation.get() }
            if (!cancellation.get()) jobFinished(params, retry)
            cancellations.remove(params.jobId, cancellation)
        }.start()
        return true
    }
    override fun onStopJob(params: JobParameters): Boolean { cancellations.remove(params.jobId)?.set(true); return true }
}
class UpdateInstallReceiver: BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) { AppUpdates.installed(context, intent) }
}
class UpdateBootReceiver: BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) { AppUpdates.schedule(context) }
}
