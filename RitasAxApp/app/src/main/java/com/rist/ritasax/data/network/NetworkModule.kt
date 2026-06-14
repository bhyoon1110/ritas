package com.rist.ritasax.data.network

import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object NetworkModule {
    private fun client(): OkHttpClient {
        val logging = HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BODY }
        return OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .writeTimeout(120, TimeUnit.SECONDS)
            .addInterceptor(logging)
            .build()
    }

    fun edgeApi(baseUrl: String): EdgeApi = Retrofit.Builder()
        .baseUrl(baseUrl.ensureTrailingSlash())
        .client(client())
        .addConverterFactory(GsonConverterFactory.create())
        .build()
        .create(EdgeApi::class.java)

    fun wifiFileApi(baseUrl: String): WifiFileApi = Retrofit.Builder()
        .baseUrl(baseUrl.ensureTrailingSlash())
        .client(client())
        .addConverterFactory(GsonConverterFactory.create())
        .build()
        .create(WifiFileApi::class.java)

    private fun String.ensureTrailingSlash(): String = if (endsWith('/')) this else "$this/"
}
