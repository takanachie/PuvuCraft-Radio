package com.puvucraft.radio.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.shape.RoundedCornerShape

val Ink0 = Color(0xFF07090A)
val Ink1 = Color(0xFF0B0F10)
val Ink2 = Color(0xFF111718)
val Ink3 = Color(0xFF182021)
val Metal = Color(0xFF222A29)
val ConsoleLine = Color(0xFF303A38)
val Paper = Color(0xFFE5E6DF)
val Muted = Color(0xFF929B97)
val Dim = Color(0xFF626B67)
val Phosphor = Color(0xFFC7E36A)
val PhosphorDeep = Color(0xFF718834)
val Amber = Color(0xFFF0AA42)
val RadioRed = Color(0xFFEE6A51)
val Cyan = Color(0xFF77C8C0)

private val RadioColors = darkColorScheme(
    primary = Phosphor,
    onPrimary = Ink0,
    primaryContainer = Color(0xFF263017),
    onPrimaryContainer = Color(0xFFE1F69B),
    secondary = Amber,
    onSecondary = Ink0,
    secondaryContainer = Color(0xFF35230F),
    onSecondaryContainer = Color(0xFFFFD89B),
    tertiary = Cyan,
    onTertiary = Ink0,
    background = Ink0,
    onBackground = Paper,
    surface = Ink2,
    onSurface = Paper,
    surfaceVariant = Ink3,
    onSurfaceVariant = Muted,
    outline = ConsoleLine,
    error = RadioRed,
    onError = Ink0,
)

private val RadioTypography = Typography(
    displayMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Black,
        fontSize = 40.sp,
        lineHeight = 43.sp,
        letterSpacing = (-0.8).sp,
    ),
    headlineLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Black,
        fontSize = 30.sp,
        lineHeight = 34.sp,
    ),
    headlineMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 24.sp,
        lineHeight = 29.sp,
    ),
    titleLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 20.sp,
        lineHeight = 25.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.Monospace,
        fontWeight = FontWeight.Bold,
        fontSize = 14.sp,
        lineHeight = 19.sp,
        letterSpacing = 0.4.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 16.sp,
        lineHeight = 24.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 14.sp,
        lineHeight = 21.sp,
    ),
    labelLarge = TextStyle(
        fontFamily = FontFamily.Monospace,
        fontWeight = FontWeight.Bold,
        fontSize = 13.sp,
        lineHeight = 17.sp,
        letterSpacing = 0.35.sp,
    ),
    labelMedium = TextStyle(
        fontFamily = FontFamily.Monospace,
        fontWeight = FontWeight.Bold,
        fontSize = 11.sp,
        lineHeight = 15.sp,
        letterSpacing = 0.7.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = FontFamily.Monospace,
        fontWeight = FontWeight.Bold,
        fontSize = 10.sp,
        lineHeight = 14.sp,
        letterSpacing = 1.2.sp,
    ),
)

private val RadioShapes = Shapes(
    extraSmall = RoundedCornerShape(2.dp),
    small = RoundedCornerShape(2.dp),
    medium = RoundedCornerShape(3.dp),
    large = RoundedCornerShape(4.dp),
    extraLarge = RoundedCornerShape(4.dp),
)

@Composable
fun PuvuCraftRadioTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = RadioColors,
        typography = RadioTypography,
        shapes = RadioShapes,
        content = content,
    )
}
