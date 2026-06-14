package com.rist.ritasax.python

import android.content.Context
import com.chaquo.python.PyException
import com.chaquo.python.Python
import com.google.gson.Gson
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.rist.ritasax.data.model.ProcessResult
import com.rist.ritasax.data.model.QCItem

class PythonBridge(context: Context) {
    private val py = Python.getInstance()
    private val gson = Gson()

    fun process(testType: String, requestId: String, filePaths: List<String>): ProcessResult {
        return try {
            val module = py.getModule("ritas_ax")
            val filesArray = JsonArray().apply { filePaths.forEach { add(it) } }
            val options = JsonObject().apply {
                addProperty("request_id", requestId)
            }
            val result = module.callAttr("process", testType, requestId, filesArray.toString(), options.toString())
            gson.fromJson(result.toString(), ProcessResult::class.java)
        } catch (e: PyException) {
            ProcessResult(
                testType = testType,
                requestId = requestId,
                summary = mapOf("error" to (e.message ?: "Python processing failed")),
                chartPaths = emptyList(),
                outputFiles = emptyList(),
                llmPayload = "{}",
                qcItems = listOf(QCItem("py_error", "Python Error", e.message ?: "Unknown", "ERROR")),
                artifacts = emptyMap(),
            )
        }
    }

    fun callExample(input: String): String {
        return try {
            val module = py.getModule("example")
            val result = module.callAttr("process_data", input)
            result.toString()
        } catch (e: PyException) {
            "Error: ${e.message}"
        }
    }
}
