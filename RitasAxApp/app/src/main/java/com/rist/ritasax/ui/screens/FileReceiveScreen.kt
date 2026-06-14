package com.rist.ritasax.ui.screens

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material.icons.filled.WifiOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.rist.ritasax.data.model.TestType
import com.rist.ritasax.ui.components.StatusCard
import com.rist.ritasax.ui.viewmodel.AppUiState
import com.rist.ritasax.ui.viewmodel.AppViewModel

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun FileReceiveScreen(state: AppUiState, viewModel: AppViewModel) {
    val selectedRemote = remember { mutableStateListOf<String>() }
    var selectedSsidForConnect by remember { mutableStateOf<String?>(null) }
    var passwordInput by remember { mutableStateOf("") }
    var showPasswordDialog by remember { mutableStateOf(false) }
    
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        if (permissions.values.all { it }) {
            viewModel.scanWifi()
        }
    }

    LaunchedEffect(Unit) {
        viewModel.checkWifiStatus()
    }

    if (showPasswordDialog && selectedSsidForConnect != null) {
        androidx.compose.material3.AlertDialog(
            onDismissRequest = { showPasswordDialog = false },
            title = { Text("WiFi 연결: $selectedSsidForConnect") },
            text = {
                Column {
                    Text("보안된 네트워크입니다. 비밀번호를 입력하세요.")
                    Spacer(modifier = Modifier.padding(8.dp))
                    OutlinedTextField(
                        value = passwordInput,
                        onValueChange = { passwordInput = it },
                        label = { Text("비밀번호") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Password)
                    )
                }
            },
            confirmButton = {
                Button(onClick = {
                    viewModel.connectToWifi(selectedSsidForConnect!!, passwordInput)
                    showPasswordDialog = false
                    passwordInput = ""
                }) {
                    Text("연결")
                }
            },
            dismissButton = {
                androidx.compose.material3.TextButton(onClick = { showPasswordDialog = false }) {
                    Text("취소")
                }
            }
        )
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        contentPadding = PaddingValues(bottom = 40.dp)
    ) {
        item {
            StatusCard(state.statusMessage, state.progress)
        }

        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = if (state.isWifiEnabled) MaterialTheme.colorScheme.surfaceVariant else MaterialTheme.colorScheme.errorContainer
                )
            ) {
                Row(
                    modifier = Modifier.padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Icon(
                        if (state.isWifiEnabled) Icons.Default.Wifi else Icons.Default.WifiOff,
                        contentDescription = null
                    )
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            if (state.isWifiEnabled) "와이파이 활성화됨" else "와이파이가 꺼져 있습니다",
                            style = MaterialTheme.typography.titleSmall
                        )
                        if (!state.isWifiEnabled) {
                            Text("설정에서 와이파이를 켜주세요.", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    if (state.isWifiEnabled) {
                        Button(onClick = {
                            val permissions = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                                arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.NEARBY_WIFI_DEVICES)
                            } else {
                                arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)
                            }
                            permissionLauncher.launch(permissions)
                        }) {
                            Icon(Icons.Default.Refresh, contentDescription = null)
                            Text("검색")
                        }
                    }
                }
            }
        }

        if (state.isWifiEnabled && state.wifiScanResults.isNotEmpty()) {
            item { Text("주변 와이파이 목록", style = MaterialTheme.typography.titleMedium) }
            items(state.wifiScanResults) { result ->
                val ssid = result.SSID.ifBlank { "(이름 없음)" }
                Card(
                    modifier = Modifier.fillMaxWidth().clickable {
                        selectedSsidForConnect = ssid
                        showPasswordDialog = true
                    }
                ) {
                    Row(modifier = Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Icon(Icons.Default.Wifi, contentDescription = null)
                        Text(ssid)
                        Spacer(modifier = Modifier.weight(1f))
                        Text("${result.level} dBm", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
        item {
            Text("시험 유형", style = MaterialTheme.typography.titleMedium)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TestType.entries.forEach { type ->
                    FilterChip(
                        selected = state.selectedTestType == type,
                        onClick = { viewModel.selectTestType(type) },
                        label = { Text(type.label) }
                    )
                }
            }
        }
        item {
            OutlinedTextField(
                value = state.wifiServerUrl,
                onValueChange = viewModel::updateWifiServerUrl,
                label = { Text("실험실 PC 파일 서버 URL") },
                modifier = Modifier.fillMaxWidth(),
                placeholder = { Text("http://192.168.x.x:8080/") }
            )
        }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = viewModel::listRemoteFiles,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("실험실 PC 파일 목록 조회")
                }
                
                if (selectedRemote.isNotEmpty()) {
                    Button(
                        onClick = { viewModel.downloadRemoteFiles(selectedRemote.toList()) },
                        modifier = Modifier.fillMaxWidth(),
                        colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.primaryContainer, contentColor = MaterialTheme.colorScheme.onPrimaryContainer)
                    ) {
                        Text("선택한 파일 태블릿으로 가져오기 (${selectedRemote.size})")
                    }
                }
            }
        }
        if (state.remoteFiles.isNotEmpty()) {
            item { Text("원격 파일", style = MaterialTheme.typography.titleMedium) }
            items(state.remoteFiles) { remote ->
                Card(modifier = Modifier.fillMaxWidth()) {
                    androidx.compose.foundation.layout.Row(modifier = Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Checkbox(
                            checked = selectedRemote.contains(remote.name),
                            onCheckedChange = { checked ->
                                if (checked) selectedRemote.add(remote.name) else selectedRemote.remove(remote.name)
                            }
                        )
                        Column {
                            Text(remote.name)
                            Text("${remote.sizeBytes} bytes / ${remote.modifiedAt}", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
        if (state.localFiles.isNotEmpty()) {
            item { Text("전처리 대기 파일", style = MaterialTheme.typography.titleMedium) }
            items(state.localFiles) { file ->
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text(file.name)
                        Text(file.localPath, style = MaterialTheme.typography.bodySmall)
                        Text("${file.sizeBytes} bytes", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
            item {
                Button(
                    onClick = viewModel::clearFiles,
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.secondary)
                ) { Text("목록 초기화") }
            }
        }
    }
}
