package com.rist.ritasax.data.repository

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import com.rist.ritasax.data.model.EmailResponse
import com.rist.ritasax.data.model.ProcessResult
import com.rist.ritasax.data.model.QCItem
import com.rist.ritasax.data.model.ReportResponse
import com.rist.ritasax.data.model.RequestInfo
import com.rist.ritasax.data.model.SelectedFile
import com.rist.ritasax.data.model.TestType
import com.rist.ritasax.data.model.UploadResponse
import com.rist.ritasax.data.network.EmailRequestDto
import com.rist.ritasax.data.network.NetworkModule
import com.rist.ritasax.data.network.RemoteFileDto
import com.rist.ritasax.data.network.ReportRequestDto
import com.rist.ritasax.data.network.UploadRequestDto
import com.rist.ritasax.python.PythonBridge
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.net.URLEncoder

class AppRepository(
    private val context: Context,
    private val pythonBridge: PythonBridge = PythonBridge(context),
) {
    private val edgeApi = NetworkModule.edgeApi(DEFAULT_EDGE_URL)

    suspend fun copyPickedUris(uris: List<Uri>): List<SelectedFile> = withContext(Dispatchers.IO) {
        uris.map { uri ->
            val file = uriToLocalFile(context.contentResolver, context.filesDir, uri)
            SelectedFile(file.name, file.absolutePath, file.length())
        }
    }

    suspend fun listRemoteFiles(serverUrl: String): List<RemoteFileDto> = withContext(Dispatchers.IO) {
        NetworkModule.wifiFileApi(serverUrl).listFiles()
    }

    suspend fun downloadRemoteFiles(serverUrl: String, fileNames: List<String>): List<SelectedFile> = withContext(Dispatchers.IO) {
        val api = NetworkModule.wifiFileApi(serverUrl)
        fileNames.map { name ->
            val safeName = URLEncoder.encode(name, "UTF-8")
            val body = api.downloadFile(safeName)
            val outFile = File(context.filesDir, name)
            body.byteStream().use { input ->
                FileOutputStream(outFile).use { output -> input.copyTo(output) }
            }
            SelectedFile(outFile.name, outFile.absolutePath, outFile.length())
        }
    }

    suspend fun process(testType: TestType, requestId: String, files: List<SelectedFile>): ProcessResult = withContext(Dispatchers.IO) {
        pythonBridge.process(
            testType = testType.label,
            requestId = requestId,
            filePaths = files.map { it.localPath },
        )
    }

    suspend fun runPythonExample(input: String): String = withContext(Dispatchers.IO) {
        pythonBridge.callExample(input)
    }

    suspend fun getRequestInfo(requestId: String): RequestInfo = edgeApi.getRequestInfo(requestId).toDomain()

    suspend fun generateReport(processResult: ProcessResult): ReportResponse {
        val dto = ReportRequestDto(
            requestId = processResult.requestId,
            testType = processResult.testType,
            llmPayload = processResult.llmPayload,
            chartPaths = processResult.chartPaths,
            imagePaths = processResult.artifacts["images"].orEmpty(),
            summary = processResult.summary,
        )
        return edgeApi.generateReport(dto).toDomain()
    }

    suspend fun uploadResult(requestId: String, localFiles: List<String>): UploadResponse {
        val result = edgeApi.uploadLocalFiles(UploadRequestDto(requestId, localFiles))
        return UploadResponse(result.success, result.message)
    }

    suspend fun sendEmail(requestId: String, message: String): EmailResponse {
        val result = edgeApi.sendEmail(EmailRequestDto(requestId, message))
        return EmailResponse(result.success, result.message)
    }

    companion object {
        const val DEFAULT_EDGE_URL = "http://10.0.2.2:8000/"

        private fun uriToLocalFile(contentResolver: ContentResolver, filesDir: File, uri: Uri): File {
            val name = queryDisplayName(contentResolver, uri) ?: "picked_${System.currentTimeMillis()}"
            val dest = File(filesDir, name)
            contentResolver.openInputStream(uri)?.use { input ->
                FileOutputStream(dest).use { output -> input.copyTo(output) }
            }
            return dest
        }

        private fun queryDisplayName(contentResolver: ContentResolver, uri: Uri): String? {
            val projection = arrayOf(android.provider.OpenableColumns.DISPLAY_NAME)
            contentResolver.query(uri, projection, null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) {
                    return cursor.getString(0)
                }
            }
            return null
        }
    }
}
