package com.rist.ritasax.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.material3.OutlinedTextField
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.unit.dp
import com.rist.ritasax.ui.components.KeyValueCard
import com.rist.ritasax.ui.components.StatusCard
import com.rist.ritasax.ui.viewmodel.AppUiState
import com.rist.ritasax.ui.viewmodel.AppViewModel

@Composable
fun ProcessScreen(state: AppUiState, viewModel: AppViewModel) {
    var exampleInput by remember { mutableStateOf("") }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        contentPadding = PaddingValues(bottom = 40.dp)
    ) {
        item { StatusCard(state.statusMessage, state.progress) }

        item {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Python 입출력 예제", style = MaterialTheme.typography.titleMedium)
                    OutlinedTextField(
                        value = exampleInput,
                        onValueChange = { exampleInput = it },
                        label = { Text("입력값") },
                        modifier = Modifier.fillMaxWidth()
                    )
                    Button(onClick = { viewModel.runPythonExample(exampleInput) }) {
                        Text("Python 호출")
                    }
                    if (state.pythonExampleResult.isNotBlank()) {
                        Text("결과: ${state.pythonExampleResult}", color = MaterialTheme.colorScheme.primary)
                    }
                }
            }
        }

        item {
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Button(
                    onClick = viewModel::processAndGenerateReport,
                    enabled = state.localFiles.isNotEmpty() && state.requestId.isNotBlank() && !state.isBusy
                ) { Text("전처리 + AI 보고서 생성") }
            }
        }
        state.processResult?.let { result ->
            item {
                KeyValueCard(
                    title = "전처리 요약",
                    items = result.summary.map { it.key to (it.value?.toString() ?: "-") }
                )
            }
            item { Text("QC 항목", style = MaterialTheme.typography.titleMedium) }
            items(result.qcItems) { qc ->
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(qc.label)
                        Text(qc.value)
                        Text("상태: ${qc.status}", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
            if (result.chartPaths.isNotEmpty()) {
                item {
                    KeyValueCard(
                        title = "생성 차트",
                        items = result.chartPaths.mapIndexed { index, path -> "차트 ${index + 1}" to path }
                    )
                }
            }
        }
        state.reportResponse?.let { report ->
            item {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("AI 보고서 결과", style = MaterialTheme.typography.titleMedium)
                        Text(report.summary)
                        Text("PPT: ${report.pptUrl}")
                        report.attachmentUrls.forEach { Text("첨부: $it", style = MaterialTheme.typography.bodySmall) }
                    }
                }
            }
        }
    }
}
