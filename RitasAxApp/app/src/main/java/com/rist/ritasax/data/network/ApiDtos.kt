package com.rist.ritasax.data.network

import com.google.gson.annotations.SerializedName
import com.rist.ritasax.data.model.ReportResponse
import com.rist.ritasax.data.model.RequestInfo


data class RequestInfoDto(
    @SerializedName("request_id") val requestId: String,
    @SerializedName("customer_name") val customerName: String,
    @SerializedName("customer_email") val customerEmail: String,
    val company: String,
    @SerializedName("test_type") val testType: String,
    @SerializedName("sample_names") val sampleNames: List<String>,
    val status: String,
) {
    fun toDomain() = RequestInfo(requestId, customerName, customerEmail, company, testType, sampleNames, status)
}

data class ReportRequestDto(
    @SerializedName("request_id") val requestId: String,
    @SerializedName("test_type") val testType: String,
    @SerializedName("llm_payload") val llmPayload: String,
    @SerializedName("chart_paths") val chartPaths: List<String>,
    @SerializedName("image_paths") val imagePaths: List<String>,
    val summary: Map<String, Any?>,
)

data class ReportResponseDto(
    @SerializedName("report_id") val reportId: String,
    val summary: String,
    @SerializedName("ppt_url") val pptUrl: String,
    @SerializedName("attachment_urls") val attachmentUrls: List<String>,
) {
    fun toDomain() = ReportResponse(reportId, summary, pptUrl, attachmentUrls)
}

data class UploadRequestDto(
    @SerializedName("request_id") val requestId: String,
    @SerializedName("file_paths") val filePaths: List<String>,
)

data class EmailRequestDto(
    @SerializedName("request_id") val requestId: String,
    val message: String,
)

data class SimpleResultDto(
    val success: Boolean,
    val message: String,
)

data class RemoteFileDto(
    val name: String,
    val sizeBytes: Long,
    val modifiedAt: String,
)
