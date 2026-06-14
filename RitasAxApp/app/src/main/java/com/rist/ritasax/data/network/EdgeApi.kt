package com.rist.ritasax.data.network

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

interface EdgeApi {
    @GET("api/lims/request/{requestId}")
    suspend fun getRequestInfo(@Path("requestId") requestId: String): RequestInfoDto

    @POST("api/process")
    suspend fun generateReport(@Body request: ReportRequestDto): ReportResponseDto

    @POST("api/lims/upload-local")
    suspend fun uploadLocalFiles(@Body request: UploadRequestDto): SimpleResultDto

    @POST("api/lims/send-email")
    suspend fun sendEmail(@Body request: EmailRequestDto): SimpleResultDto
}
