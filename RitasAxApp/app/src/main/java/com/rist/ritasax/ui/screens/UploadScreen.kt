package com.rist.ritasax.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.rist.ritasax.ui.components.StatusCard
import com.rist.ritasax.ui.viewmodel.AppUiState
import com.rist.ritasax.ui.viewmodel.AppViewModel

@Composable
fun UploadScreen(state: AppUiState, viewModel: AppViewModel) {
    val outputFiles = buildList {
        addAll(state.processResult?.outputFiles.orEmpty())
        addAll(state.processResult?.chartPaths.orEmpty())
    }
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        contentPadding = PaddingValues(bottom = 40.dp)
    ) {
        item { StatusCard(state.statusMessage, state.progress) }
        item {
            Button(onClick = viewModel::uploadToLims, enabled = outputFiles.isNotEmpty() && !state.isBusy) { Text("LIMS 업로드 실행") }
        }
        item {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("업로드 대상", style = MaterialTheme.typography.titleMedium)
                    if (outputFiles.isEmpty()) Text("전처리 또는 보고서 생성 결과가 없습니다.")
                    outputFiles.forEach { Text(it, style = MaterialTheme.typography.bodySmall) }
                }
            }
        }
    }
}
