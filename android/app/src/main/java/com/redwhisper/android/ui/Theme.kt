package com.redwhisper.android.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val BrandRed = Color(0xFFC8102E)

private val Light = lightColorScheme(
    primary = BrandRed,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFFFDAD7),
    onPrimaryContainer = Color(0xFF410004),
    secondaryContainer = Color(0xFFFFE3E0),
    onSecondaryContainer = Color(0xFF410004),
    background = Color(0xFFF3F3F5),
    surface = Color.White,
    surfaceContainer = Color(0xFFF3F3F5),
    surfaceVariant = Color(0xFFEDE7E6),
)

private val Dark = darkColorScheme(
    primary = Color(0xFFFFB3AC),
    onPrimary = Color(0xFF68000B),
    primaryContainer = Color(0xFF930016),
    onPrimaryContainer = Color(0xFFFFDAD7),
    secondaryContainer = Color(0xFF5C1A1A),
    onSecondaryContainer = Color(0xFFFFDAD7),
    background = Color(0xFF111113),
    surface = Color(0xFF1E1E21),
    surfaceContainer = Color(0xFF1A1A1D),
)

@Composable
fun RedWhisperTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = if (isSystemInDarkTheme()) Dark else Light, content = content)
}
