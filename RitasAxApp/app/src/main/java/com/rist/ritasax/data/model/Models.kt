package com.rist.ritasax.data.model

import java.io.File

enum class TestType(val label: String) {
    TEM("TEM"), GDMS("GD-MS"), XRD("XRD"), FTIR("FT-IR"), XPS("XPS");

    companion object {
        fun fromLabel(label: String): TestType = entries.firstOrNull { it.label == label } ?: TEM
    }
}

data class SelectedFile(
    val name: String,
    val localPath: String,
    val sizeBytes: Long
)

data class RequestInfo(
    val requestId: String,
    val customerName: String,
    val customerEmail: String,
    val company: String,
    val testType: String,
    val sampleNames: List<String>,
    val status: String,
)

data class QCItem(
    val key: String,
    val label: String,
    val value: String,
    val status: String,
)

data class ProcessResult(
    val testType: String,
    val requestId: String,
    val summary: Map<String, Any?>,
    val chartPaths: List<String>,
    val outputFiles: List<String>,
    val llmPayload: String,
    val qcItems: List<QCItem>,
    val artifacts: Map<String, List<String>>,
)

data class ReportResponse(
    val reportId: String,
    val summary: String,
    val pptUrl: String,
    val attachmentUrls: List<String>,
)

data class UploadResponse(
    val success: Boolean,
    val message: String,
)

data class EmailResponse(
    val success: Boolean,
    val message: String,
)
