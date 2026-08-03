package com.puvucraft.radio

enum class PlayerStreamFormat(
    val wireValue: String,
) {
    AAC("aac"),
    FLAC("flac"),
    ;

    companion object {
        fun fromWireValue(value: String?): PlayerStreamFormat? =
            entries.firstOrNull { it.wireValue == value?.lowercase() }
    }
}
