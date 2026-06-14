package com.rist.ritasax.ui.viewmodel

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.Uri
import android.net.wifi.ScanResult
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSpecifier
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.rist.ritasax.data.model.ProcessResult
import com.rist.ritasax.data.model.ReportResponse
import com.rist.ritasax.data.model.RequestInfo
import com.rist.ritasax.data.model.SelectedFile
import com.rist.ritasax.data.model.TestType
import com.rist.ritasax.data.network.RemoteFileDto
import com.rist.ritasax.data.repository.AppRepository
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class AppUiState(
    val selectedTestType: TestType = TestType.TEM,
    val requestId: String = "",
    val edgeUrl: String = AppRepository.DEFAULT_EDGE_URL,
    val wifiServerUrl: String = "http://192.168.0.10:8080/",
    val localFiles: List<SelectedFile> = emptyList(),
    val remoteFiles: List<RemoteFileDto> = emptyList(),
    val requestInfo: RequestInfo? = null,
    val processResult: ProcessResult? = null,
    val reportResponse: ReportResponse? = null,
    val statusMessage: String = "대기 중",
    val progress: Int = 0,
    val isBusy: Boolean = false,
    val emailBody: String = "시험 결과를 송부드립니다.",
    val pythonExampleResult: String = "",
    val wifiScanResults: List<ScanResult> = emptyList(),
    val isWifiEnabled: Boolean = false,
)

class AppViewModel(application: android.app.Application) : AndroidViewModel(application) {
    private val repository = AppRepository(application.applicationContext)
    private val wifiManager = application.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
    private val connectivityManager = application.applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    private val _uiState = MutableStateFlow(AppUiState())
    val uiState: StateFlow<AppUiState> = _uiState.asStateFlow()

    private val wifiStateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action == WifiManager.WIFI_STATE_CHANGED_ACTION) {
                checkWifiStatus()
            }
        }
    }

    init {
        checkWifiStatus()
        val filter = IntentFilter(WifiManager.WIFI_STATE_CHANGED_ACTION)
        application.registerReceiver(wifiStateReceiver, filter)
    }

    override fun onCleared() {
        super.onCleared()
        runCatching { getApplication<android.app.Application>().unregisterReceiver(wifiStateReceiver) }
    }

    fun checkWifiStatus() {
        val enabled = wifiManager.isWifiEnabled
        _uiState.tryEmit(_uiState.value.copy(isWifiEnabled = enabled))
    }

    fun scanWifi() {
        viewModelScope.launch {
            if (!wifiManager.isWifiEnabled) {
                _uiState.emit(_uiState.value.copy(statusMessage = "와이파이가 꺼져 있습니다. 켜주세요."))
                return@launch
            }
            _uiState.emit(_uiState.value.copy(statusMessage = "주변 와이파이 검색 중..."))
            wifiManager.startScan()
            delay(1000)
            val results = wifiManager.scanResults
            _uiState.emit(_uiState.value.copy(wifiScanResults = results, statusMessage = "와이파이 검색 완료"))
        }
    }

    fun connectToWifi(ssid: String, password: String? = null) {
        viewModelScope.launch {
            _uiState.emit(_uiState.value.copy(statusMessage = "와이파이 연결 요청 중: $ssid"))
            
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val builder = WifiNetworkSpecifier.Builder()
                    .setSsid(ssid)
                
                if (!password.isNullOrEmpty()) {
                    builder.setWpa2Passphrase(password)
                }

                val specifier = builder.build()
                val request = NetworkRequest.Builder()
                    .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                    .setNetworkSpecifier(specifier)
                    .build()
                
                // This will trigger a system UI for the user to confirm connection
                connectivityManager.requestNetwork(request, object : ConnectivityManager.NetworkCallback() {
                    override fun onAvailable(network: android.net.Network) {
                        super.onAvailable(network)
                        connectivityManager.bindProcessToNetwork(network)
                        updateStatusMessage("와이파이 연결 성공: $ssid")
                    }
                    override fun onUnavailable() {
                        super.onUnavailable()
                        updateStatusMessage("와이파이 연결 실패 또는 취소됨")
                    }
                })
            } else {
                // Legacy way (simplified, might need password handling)
                updateStatusMessage("안드로이드 10 미만 버전은 시스템 설정에서 직접 연결해 주세요.")
            }
        }
    }

    fun updateRequestId(value: String) = _uiState.tryEmit(_uiState.value.copy(requestId = value))
    fun updateEdgeUrl(value: String) = _uiState.tryEmit(_uiState.value.copy(edgeUrl = value))
    fun updateWifiServerUrl(value: String) = _uiState.tryEmit(_uiState.value.copy(wifiServerUrl = value))
    fun updateEmailBody(value: String) = _uiState.tryEmit(_uiState.value.copy(emailBody = value))
    fun selectTestType(testType: TestType) = _uiState.tryEmit(_uiState.value.copy(selectedTestType = testType))

    fun addPickedUris(uris: List<Uri>) {
        viewModelScope.launch {
            busy("파일 복사 중...", 10)
            runCatching { repository.copyPickedUris(uris) }
                .onSuccess { copied ->
                    val merged = (_uiState.value.localFiles + copied).distinctBy { it.localPath }
                    _uiState.emit(_uiState.value.copy(localFiles = merged, statusMessage = "파일 ${copied.size}개 준비 완료", progress = 20, isBusy = false))
                }
                .onFailure { fail(it) }
        }
    }

    fun listRemoteFiles() {
        viewModelScope.launch {
            busy("실험실 PC 조회 중...", 10)
            runCatching { repository.listRemoteFiles(_uiState.value.wifiServerUrl) }
                .onSuccess { list -> _uiState.emit(_uiState.value.copy(remoteFiles = list, statusMessage = "원격 파일 ${list.size}개 조회", progress = 20, isBusy = false)) }
                .onFailure { fail(it) }
        }
    }

    fun downloadRemoteFiles(selected: List<String>) {
        viewModelScope.launch {
            busy("원격 파일 다운로드 중...", 20)
            runCatching { repository.downloadRemoteFiles(_uiState.value.wifiServerUrl, selected) }
                .onSuccess { files ->
                    val merged = (_uiState.value.localFiles + files).distinctBy { it.localPath }
                    _uiState.emit(_uiState.value.copy(localFiles = merged, statusMessage = "다운로드 완료", progress = 35, isBusy = false))
                }
                .onFailure { fail(it) }
        }
    }

    fun fetchRequest() {
        val requestId = _uiState.value.requestId.ifBlank { return }
        viewModelScope.launch {
            busy("의뢰번호 조회 중...", 25)
            runCatching { repository.getRequestInfo(requestId) }
                .onSuccess { info ->
                    _uiState.emit(_uiState.value.copy(
                        requestInfo = info,
                        statusMessage = "의뢰 조회 완료",
                        progress = 40,
                        isBusy = false,
                        selectedTestType = TestType.fromLabel(info.testType)
                    ))
                }
                .onFailure { fail(it) }
        }
    }

    fun processAndGenerateReport() {
        val state = _uiState.value
        if (state.requestId.isBlank() || state.localFiles.isEmpty()) return
        viewModelScope.launch {
            busy("태블릿 전처리 중...", 45)
            runCatching {
                repository.process(state.selectedTestType, state.requestId, state.localFiles)
            }.onSuccess { processed ->
                _uiState.emit(_uiState.value.copy(processResult = processed, statusMessage = "전처리 완료, AI 보고서 생성 중...", progress = 70, isBusy = true))
                runCatching { repository.generateReport(processed) }
                    .onSuccess { report ->
                        _uiState.emit(_uiState.value.copy(reportResponse = report, statusMessage = "보고서 생성 완료", progress = 100, isBusy = false))
                    }
                    .onFailure { fail(it) }
            }.onFailure { fail(it) }
        }
    }

    fun uploadToLims() {
        val state = _uiState.value
        val payload = buildList {
            addAll(state.processResult?.outputFiles.orEmpty())
            addAll(state.processResult?.chartPaths.orEmpty())
        }.distinct()
        if (state.requestId.isBlank() || payload.isEmpty()) return
        viewModelScope.launch {
            busy("LIMS 업로드 중...", 85)
            runCatching { repository.uploadResult(state.requestId, payload) }
                .onSuccess { result -> _uiState.emit(_uiState.value.copy(statusMessage = result.message, progress = 95, isBusy = false)) }
                .onFailure { fail(it) }
        }
    }

    fun sendEmail() {
        val state = _uiState.value
        if (state.requestId.isBlank()) return
        viewModelScope.launch {
            busy("의뢰자 메일 발송 중...", 95)
            runCatching { repository.sendEmail(state.requestId, state.emailBody) }
                .onSuccess { result -> _uiState.emit(_uiState.value.copy(statusMessage = result.message, progress = 100, isBusy = false)) }
                .onFailure { fail(it) }
        }
    }

    fun clearFiles() = _uiState.tryEmit(_uiState.value.copy(localFiles = emptyList(), remoteFiles = emptyList(), processResult = null, reportResponse = null))

    fun updateStatusMessage(message: String) {
        _uiState.tryEmit(_uiState.value.copy(statusMessage = message))
    }

    fun runPythonExample(input: String) {
        viewModelScope.launch {
            val result = repository.runPythonExample(input)
            _uiState.emit(_uiState.value.copy(pythonExampleResult = result))
        }
    }

    fun exitApp() {
        android.os.Process.killProcess(android.os.Process.myPid())
    }

    private suspend fun busy(message: String, progress: Int) {
        _uiState.emit(_uiState.value.copy(isBusy = true, statusMessage = message, progress = progress))
    }

    private fun fail(throwable: Throwable) {
        viewModelScope.launch {
            _uiState.emit(_uiState.value.copy(
                isBusy = false,
                statusMessage = throwable.message ?: "오류가 발생했습니다.",
            ))
        }
    }
}
