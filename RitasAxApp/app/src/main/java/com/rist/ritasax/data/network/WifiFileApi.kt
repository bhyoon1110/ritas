package com.rist.ritasax.data.network

import okhttp3.ResponseBody
import retrofit2.http.GET
import retrofit2.http.Path

interface WifiFileApi {
    @GET("list")
    suspend fun listFiles(): List<RemoteFileDto>

    @GET("download/{name}")
    suspend fun downloadFile(@Path(value = "name", encoded = true) name: String): ResponseBody
}
