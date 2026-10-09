package app.arienx.arienx_mobile

import android.Manifest
import android.content.pm.PackageManager
import android.media.*
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.os.Handler
import android.os.Looper
import io.flutter.embedding.android.FlutterActivity
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodChannel
import java.util.concurrent.LinkedBlockingQueue
import kotlin.math.sqrt

class LiveAudio(private val activity: FlutterActivity): EventChannel.StreamHandler {
    private val main = Handler(Looper.getMainLooper())
    private val manager = activity.getSystemService(android.content.Context.AUDIO_SERVICE) as AudioManager
    private var sink: EventChannel.EventSink? = null
    private var permission: MethodChannel.Result? = null
    private var recorder: AudioRecord? = null
    private var player: AudioTrack? = null
    private var echo: AcousticEchoCanceler? = null
    private var noise: NoiseSuppressor? = null
    private var captureThread: Thread? = null
    @Volatile private var running = false
    @Volatile private var recording = false
    @Volatile private var generation = 0
    private var configured = false
    private var oldMode = AudioManager.MODE_NORMAL
    private var oldSpeaker = false
    private val queue = LinkedBlockingQueue<Pair<Int,ByteArray>>(128)
    private val outputLock = Any()
    private var written = 0L
    private var headBase = 0L
    private var announced = false
    private val focusListener=AudioManager.OnAudioFocusChangeListener { change ->
        if(change == AudioManager.AUDIOFOCUS_LOSS || change == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT) { stop(); emit(mapOf("type" to "error","message" to "Call audio interrupted")) }
    }
    private fun emit(value: Map<String,Any>) { main.post { sink?.success(value) } }
    override fun onListen(arguments: Any?, events: EventChannel.EventSink) { sink = events }
    override fun onCancel(arguments: Any?) { stop(); sink = null }
    @Suppress("DEPRECATION")
    fun handle(call: io.flutter.plugin.common.MethodCall, result: MethodChannel.Result) {
        try { when(call.method) {
            "start" -> {
                val capture=call.argument<Boolean>("capture") != false
                if (capture && activity.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                    if (permission != null) { result.error("busy","Microphone permission is pending",null); return }
                    permission=result; activity.requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO),7714)
                } else { start(capture); result.success(true) }
            }
            "play" -> {
                val data=call.arguments as? ByteArray ?: error("Invalid audio")
                require(data.size <= 192000 && data.size % 2 == 0)
                if (running && !queue.offer(Pair(generation,data))) error("Audio playback is overloaded")
                result.success(null)
            }
            "clear" -> { clear(); result.success(null) }
            "mute" -> { if (call.arguments == true) stopCapture() else if(running) startCapture(); result.success(null) }
            "speaker" -> { manager.isSpeakerphoneOn=call.arguments == true; result.success(null) }
            "stop" -> { stop(); result.success(null) }
            else -> result.notImplemented()
        } } catch (_: Exception) { stop(); result.error("audio","Phone audio could not start. Check microphone permission and retry.",null) }
    }
    fun permissionResult(request: Int, grants: IntArray): Boolean {
        if(request != 7714) return false
        val result=permission ?: return true; permission=null
        if(grants.isNotEmpty() && grants[0] == PackageManager.PERMISSION_GRANTED) {
            try { start(); result?.success(true) } catch (_:Exception) { stop(); result?.error("audio","Phone audio unavailable",null) }
        } else result?.error("permission","Allow microphone access to start a voice call",null)
        return true
    }
    private fun level(data: ByteArray): Double {
        var sum=0.0
        for(i in 0 until data.size-1 step 2) { val sample=((data[i].toInt() and 255) or (data[i+1].toInt() shl 8)).toShort().toDouble()/32768.0; sum+=sample*sample }
        return sqrt(sum/(data.size/2).coerceAtLeast(1)).coerceIn(0.0,1.0)
    }
    @Suppress("DEPRECATION")
    private fun start(capture: Boolean = true) {
        if(running) return
        oldMode=manager.mode; oldSpeaker=manager.isSpeakerphoneOn; configured=true
        check(manager.requestAudioFocus(focusListener,AudioManager.STREAM_VOICE_CALL,AudioManager.AUDIOFOCUS_GAIN_TRANSIENT) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED)
        manager.mode=AudioManager.MODE_IN_COMMUNICATION
        val headset=manager.getDevices(AudioManager.GET_DEVICES_OUTPUTS).any { it.type in listOf(AudioDeviceInfo.TYPE_WIRED_HEADSET,AudioDeviceInfo.TYPE_WIRED_HEADPHONES,AudioDeviceInfo.TYPE_BLUETOOTH_SCO,AudioDeviceInfo.TYPE_USB_HEADSET) }
        manager.isSpeakerphoneOn=!headset
        player=AudioTrack.Builder().setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setAudioFormat(AudioFormat.Builder().setSampleRate(24000).setEncoding(AudioFormat.ENCODING_PCM_16BIT).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
            .setBufferSizeInBytes(AudioTrack.getMinBufferSize(24000,AudioFormat.CHANNEL_OUT_MONO,AudioFormat.ENCODING_PCM_16BIT).coerceAtLeast(4096)*2)
            .setTransferMode(AudioTrack.MODE_STREAM).build()
        check(player?.state == AudioTrack.STATE_INITIALIZED)
        player!!.play(); running=true; written=0; headBase=0; announced=false
        val track=player!!
        Thread {
            try {
                while(running && player === track) {
                    val packet=queue.poll(100,java.util.concurrent.TimeUnit.MILLISECONDS)
                    if(packet == null) {
                        if(announced && ((track.playbackHeadPosition.toLong() and 0xffffffffL)-headBase) >= written) { announced=false; emit(mapOf("type" to "speaker","playing" to false,"level" to 0.0)) }
                        continue
                    }
                    if(packet.first != generation) continue
                    announced=true; emit(mapOf("type" to "speaker","playing" to true,"level" to level(packet.second)))
                    var offset=0
                    while(offset < packet.second.size && running && packet.first == generation) {
                        synchronized(outputLock) {
                            if(packet.first == generation && running) {
                                val n=track.write(packet.second,offset,(packet.second.size-offset).coerceAtMost(1920),AudioTrack.WRITE_BLOCKING)
                                check(n>0); offset+=n; written+=n/2
                            }
                        }
                    }
                }
            } catch (_:Exception) { if(running && player === track) emit(mapOf("type" to "error","message" to "Phone speaker disconnected")) }
        }.apply { name="ArienX-live-speaker"; start() }
        if(capture) startCapture()
    }
    private fun startCapture() {
        if(recording) return
        val min=AudioRecord.getMinBufferSize(16000,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT)
        val rec=AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION,16000,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT,min.coerceAtLeast(2560)*2)
        check(rec.state == AudioRecord.STATE_INITIALIZED)
        recorder=rec
        if(AcousticEchoCanceler.isAvailable()) echo=AcousticEchoCanceler.create(rec.audioSessionId)?.apply { setEnabled(true) }
        if(NoiseSuppressor.isAvailable()) noise=NoiseSuppressor.create(rec.audioSessionId)?.apply { setEnabled(true) }
        recording=true; rec.startRecording()
        captureThread=Thread {
            val buffer=ByteArray(640)
            try { while(recording && recorder === rec) {
                val n=rec.read(buffer,0,buffer.size,AudioRecord.READ_BLOCKING)
                if(n>0 && recording && recorder === rec) {
                    val bytes=buffer.copyOf(n)
                    main.post { if(recording && recorder === rec) sink?.success(mapOf("type" to "mic","data" to bytes,"level" to level(bytes))) }
                } else if(recording && recorder === rec) error("Microphone disconnected")
            } } catch (_:Exception) { if(recording && recorder === rec) emit(mapOf("type" to "error","message" to "Phone microphone disconnected")) }
        }.apply { name="ArienX-live-microphone"; start() }
    }
    private fun stopCapture() {
        recording=false
        val rec=recorder; recorder=null
        try { rec?.stop() } catch (_:Exception) {}
        captureThread?.join(300); captureThread=null
        echo?.release(); echo=null; noise?.release(); noise=null; rec?.release()
    }
    fun clear() { synchronized(outputLock) {
        generation++; queue.clear(); player?.pause(); player?.flush()
        written=0; headBase=player?.playbackHeadPosition?.toLong()?.and(0xffffffffL) ?: 0; announced=false
        if(running) player?.play()
        emit(mapOf("type" to "speaker","playing" to false,"level" to 0.0))
    } }
    @Suppress("DEPRECATION")
    fun stop() {
        running=false; stopCapture()
        synchronized(outputLock) { generation++; queue.clear(); try { player?.pause(); player?.flush(); player?.release() } catch (_:Exception) {}; player=null }
        if(configured) { manager.abandonAudioFocus(focusListener); manager.mode=oldMode; manager.isSpeakerphoneOn=oldSpeaker; configured=false }
        permission?.error("cancelled","Call ended",null); permission=null
    }
}
