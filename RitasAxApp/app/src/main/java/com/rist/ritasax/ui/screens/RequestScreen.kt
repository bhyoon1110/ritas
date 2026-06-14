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
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.rist.ritasax.ui.components.ChipRow
import com.rist.ritasax.ui.components.StatusCard
import com.rist.ritasax.ui.viewmodel.AppUiState
import com.rist.ritasax.ui.viewmodel.AppViewModel

@Composable
fun RequestScreen(state: AppUiState, viewModel: AppViewModel) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        contentPadding = PaddingValues(bottom = 40.dp)
    ) {
        item { StatusCard(state.statusMessage, state.progress) }
        item {
            OutlinedTextField(
                value = state.requestId,
                onValueChange = viewModel::updateRequestId,
                label = { Text("의뢰번호") },
                modifier = Modifier.fillMaxWidth(),
                supportingText = { Text("예: TK25-05B-026") }
            )
        }
        item {
            Button(onClick = viewModel::fetchRequest) { Text("의뢰 정보 조회") }
        }
        state.requestInfo?.let { info ->
            item {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("의뢰 정보", style = MaterialTheme.typography.titleMedium)
                        Text("고객: ${info.customerName} (${info.company})")
                        Text("이메일: ${info.customerEmail}")
                        Text("시험: ${info.testType}")
                        Text("상태: ${info.status}")
                        ChipRow(info.sampleNames)
                    }
                }
            }
        }
    }
}
