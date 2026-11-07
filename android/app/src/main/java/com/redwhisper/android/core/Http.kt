package com.redwhisper.android.core

import java.io.Closeable
import java.io.IOException
import org.json.JSONException
import org.json.JSONObject
import org.json.JSONTokener

class HttpRequest(
    val url: String,
    val method: String = "POST",
    val headers: Map<String, String> = emptyMap(),
    val body: ByteArray? = null,
    val contentType: String? = null,
    val timeoutSeconds: Long = 120,
)

class HttpResponse(val code: Int, val body: ByteArray) {
    val isSuccessful: Boolean get() = code in 200..299
}

/** A streamed response: [lines] is read lazily on success; [errorBody] holds the body otherwise. */
class StreamedResponse(val code: Int, val lines: Sequence<String>, val errorBody: ByteArray) {
    val isSuccessful: Boolean get() = code in 200..299
}

/** A blocking WebSocket, mirroring the synchronous client the Mac app uses. */
interface RealtimeConnection : Closeable {
    /** Returns the next text message, or null when [timeoutMillis] passes first. */
    @Throws(IOException::class)
    fun receive(timeoutMillis: Long): String?

    @Throws(IOException::class)
    fun send(text: String)
}

/** Network seam so the provider code runs against fakes in local unit tests. */
interface HttpTransport {
    @Throws(IOException::class)
    fun execute(request: HttpRequest): HttpResponse

    @Throws(IOException::class)
    fun <T> stream(request: HttpRequest, read: (StreamedResponse) -> T): T

    @Throws(IOException::class)
    fun openWebSocket(url: String, headers: Map<String, String>, openTimeoutMillis: Long): RealtimeConnection
}

internal fun parseJsonObject(body: ByteArray): JSONObject? = parseJsonObject(body.toString(Charsets.UTF_8))

internal fun parseJsonObject(text: String): JSONObject? = try {
    JSONTokener(text).nextValue() as? JSONObject
} catch (_: JSONException) {
    null
}

/** Mirrors `OpenAITranscriber._api_error_message`: a short, key-free reason from an error body. */
internal fun apiErrorMessage(body: ByteArray): String {
    val payload = parseJsonObject(body) ?: return "request rejected"
    val message = payload.optJSONObject("error")?.opt("message")
    if (message is String) return message.take(300)
    val detail = payload.opt("detail")
    if (detail is String) return detail.take(300)
    return "request rejected"
}

internal fun multipartBody(
    boundary: String,
    fields: Map<String, String>,
    file: ByteArray,
    fileName: String,
): ByteArray {
    val output = java.io.ByteArrayOutputStream()
    fun write(text: String) = output.write(text.toByteArray(Charsets.UTF_8))
    for ((name, value) in fields) {
        write("--$boundary\r\n")
        write("Content-Disposition: form-data; name=\"$name\"\r\n\r\n")
        write(value)
        write("\r\n")
    }
    write("--$boundary\r\n")
    write("Content-Disposition: form-data; name=\"file\"; filename=\"$fileName\"\r\n")
    write("Content-Type: audio/wav\r\n\r\n")
    output.write(file)
    write("\r\n")
    write("--$boundary--\r\n")
    return output.toByteArray()
}
