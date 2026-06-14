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
import com.rist.ritasax.ui.components.StatusCard
import com.rist.ritasax.ui.viewmodel.AppUiState
import com.rist.ritasax.ui.viewmodel.AppViewModel

@Composable
fun EmailScreen(state: AppUiState, viewModel: AppViewModel) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        contentPadding = PaddingValues(bottom = 40.dp)
    ) {
        item { StatusCard(state.statusMessage, state.progress) }
        item {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("의뢰자 발송", style = MaterialTheme.typography.titleMedium)
                    Text("대상: ${state.requestInfo?.customerEmail ?: "미조회"}")
                }
            }
        }
        item {
            OutlinedTextField(
                value = state.emailBody,
                onValueChange = viewModel::updateEmailBody,
                label = { Text("메일 본문") },
                minLines = 5,
                modifier = Modifier.fillMaxWidth()
            )
        }
        item {
            Button(onClick = viewModel::sendEmail, enabled = !state.isBusy && state.requestId.isNotBlank()) { Text("메일 발송") }
        }
    }
}
