package com.redwhisper.android.net

import com.redwhisper.android.core.HttpRequest
import com.redwhisper.android.core.HttpResponse
import com.redwhisper.android.core.HttpTransport
import com.redwhisper.android.core.RealtimeConnection
import com.redwhisper.android.core.StreamedResponse
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/** Production network transport. Requests carry keys only in headers, never in URLs or logs. */
class OkHttpTransport(private val client: OkHttpClient = OkHttpClient()) : HttpTransport {

    private fun clientFor(timeoutSeconds: Long): OkHttpClient = client.newBuilder()
        .callTimeout(timeoutSeconds, TimeUnit.SECONDS)
        .readTimeout(timeoutSeconds, TimeUnit.SECONDS)
        .build()

    private fun build(request: HttpRequest): Request {
        val builder = Request.Builder().url(request.url)
        request.headers.forEach { (name, value) -> builder.header(name, value) }
        val body = request.body
        if (request.method == "GET" || body == null) {
            builder.get()
        } else {
            builder.method(request.method, body.toRequestBody(request.contentType?.toMediaTypeOrNull()))
        }
        return builder.build()
    }

    override fun execute(request: HttpRequest): HttpResponse =
        clientFor(request.timeoutSeconds).newCall(build(request)).execute().use { response ->
            HttpResponse(response.code, response.body?.bytes() ?: ByteArray(0))
        }

    override fun <T> stream(request: HttpRequest, read: (StreamedResponse) -> T): T {
        // Streams stay open as long as the model writes; the reader enforces its own deadline.
        val streamingClient = client.newBuilder()
            .callTimeout(0, TimeUnit.SECONDS)
            .readTimeout(request.timeoutSeconds, TimeUnit.SECONDS)
            .build()
        return streamingClient.newCall(build(request)).execute().use { response ->
            if (!response.isSuccessful) {
                read(StreamedResponse(response.code, emptySequence(), response.body?.bytes() ?: ByteArray(0)))
            } else {
                read(StreamedResponse(response.code, lines(response), ByteArray(0)))
            }
        }
    }

    private fun lines(response: Response): Sequence<String> = sequence {
        val source = response.body?.source() ?: return@sequence
        while (true) {
            val line = source.readUtf8Line() ?: break
            yield(line)
        }
    }

    override fun openWebSocket(
        url: String,
        headers: Map<String, String>,
        openTimeoutMillis: Long,
    ): RealtimeConnection {
        val builder = Request.Builder().url(url)
        headers.forEach { (name, value) -> builder.header(name, value) }
        val connection = OkHttpRealtimeConnection()
        val socket = client.newBuilder()
            .connectTimeout(openTimeoutMillis, TimeUnit.MILLISECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build()
            .newWebSocket(builder.build(), connection.listener)
        connection.attach(socket)
        connection.awaitOpen(openTimeoutMillis)
        return connection
    }
}

private class OkHttpRealtimeConnection : RealtimeConnection {
    private sealed interface Event {
        data class Message(val text: String) : Event
        data class Failure(val reason: String) : Event
    }

    private val events = LinkedBlockingQueue<Event>()
    private val opened = CountDownLatch(1)
    @Volatile private var openFailure: String? = null
    private lateinit var socket: WebSocket

    val listener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            opened.countDown()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            events.put(Event.Message(text))
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            events.put(Event.Failure("connection closed ($code)"))
            webSocket.close(1000, null)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            // Error text never includes request headers, so the API key cannot leak here.
            val reason = response?.let { "HTTP ${it.code}" } ?: (t.message ?: t.javaClass.simpleName)
            openFailure = reason
            events.put(Event.Failure(reason))
            opened.countDown()
        }
    }

    fun attach(webSocket: WebSocket) {
        socket = webSocket
    }

    fun awaitOpen(timeoutMillis: Long) {
        if (!opened.await(timeoutMillis, TimeUnit.MILLISECONDS)) {
            socket.cancel()
            throw IOException("timed out connecting")
        }
        openFailure?.let { throw IOException(it) }
    }

    override fun receive(timeoutMillis: Long): String? =
        when (val event = events.poll(timeoutMillis, TimeUnit.MILLISECONDS)) {
            null -> null
            is Event.Message -> event.text
            is Event.Failure -> throw IOException(event.reason)
        }

    override fun send(text: String) {
        if (!socket.send(text)) throw IOException("connection closed")
    }

    override fun close() {
        socket.close(1000, null)
    }
}
