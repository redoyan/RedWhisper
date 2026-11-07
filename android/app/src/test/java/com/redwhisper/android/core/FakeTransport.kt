package com.redwhisper.android.core

import java.io.IOException
import java.util.ArrayDeque

/** Records requests and replies with queued responses, like the Python tests' patched urlopen. */
class FakeTransport : HttpTransport {
    val requests = mutableListOf<HttpRequest>()
    private val responses = ArrayDeque<() -> HttpResponse>()
    private val streams = ArrayDeque<StreamedResponse>()
    var socket: FakeSocket? = null
    var socketUrl: String? = null
    var socketHeaders: Map<String, String> = emptyMap()

    fun reply(code: Int, body: String) = apply { responses.add { HttpResponse(code, body.toByteArray()) } }

    fun fail(message: String) = apply { responses.add { throw IOException(message) } }

    fun replyStream(code: Int, lines: List<String>, errorBody: String = "") = apply {
        streams.add(StreamedResponse(code, lines.asSequence(), errorBody.toByteArray()))
    }

    override fun execute(request: HttpRequest): HttpResponse {
        requests.add(request)
        return responses.removeFirst().invoke()
    }

    override fun <T> stream(request: HttpRequest, read: (StreamedResponse) -> T): T {
        requests.add(request)
        return read(streams.removeFirst())
    }

    override fun openWebSocket(url: String, headers: Map<String, String>, openTimeoutMillis: Long): RealtimeConnection {
        socketUrl = url
        socketHeaders = headers
        return socket ?: error("No fake socket configured")
    }

    fun lastBody(): String = requests.last().body!!.toString(Charsets.UTF_8)
}

class FakeSocket(private val incoming: MutableList<String?>) : RealtimeConnection {
    val sent = mutableListOf<String>()
    var closed = false

    override fun receive(timeoutMillis: Long): String? = if (incoming.isEmpty()) null else incoming.removeAt(0)

    override fun send(text: String) {
        sent.add(text)
    }

    override fun close() {
        closed = true
    }
}
