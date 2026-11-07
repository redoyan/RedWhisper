package com.redwhisper.android.audio

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build

/** A selectable microphone. [key] survives reconnects, like the Mac app's name matching. */
data class InputDeviceOption(val key: String, val label: String, val device: AudioDeviceInfo) {
    val isBluetooth: Boolean get() = device.isBluetoothInput()
}

internal fun AudioDeviceInfo.isBluetoothInput(): Boolean =
    type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO ||
        (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && type == AudioDeviceInfo.TYPE_BLE_HEADSET)

object InputDevices {
    fun list(context: Context): List<InputDeviceOption> {
        val manager = context.getSystemService(AudioManager::class.java)
        val options = linkedMapOf<String, InputDeviceOption>()
        for (device in manager.getDevices(AudioManager.GET_DEVICES_INPUTS)) {
            val name = device.productName?.toString()?.trim().orEmpty()
            val (key, label) = when {
                // Phones expose several built-in mics (bottom, back); they are one choice here.
                device.type == AudioDeviceInfo.TYPE_BUILTIN_MIC -> "builtin" to "Phone microphone"
                device.isBluetoothInput() -> "bluetooth:$name" to "Bluetooth · ${name.ifEmpty { "headset" }}"
                device.type == AudioDeviceInfo.TYPE_WIRED_HEADSET -> "wired:$name" to "Wired headset"
                device.type in USB_TYPES -> "usb:$name" to "USB · ${name.ifEmpty { "microphone" }}"
                else -> continue
            }
            options.putIfAbsent(key, InputDeviceOption(key, label, device))
        }
        return options.values.toList()
    }

    fun find(context: Context, key: String?): InputDeviceOption? =
        key?.let { wanted -> list(context).firstOrNull { it.key == wanted } }

    private val USB_TYPES = setOf(
        AudioDeviceInfo.TYPE_USB_DEVICE,
        AudioDeviceInfo.TYPE_USB_HEADSET,
        AudioDeviceInfo.TYPE_USB_ACCESSORY,
    )
}
