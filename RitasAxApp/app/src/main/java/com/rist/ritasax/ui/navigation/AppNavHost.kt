package com.rist.ritasax.ui.navigation

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Email
import androidx.compose.material.icons.filled.FileDownload
import androidx.compose.material.icons.filled.ListAlt
import androidx.compose.material.icons.filled.Publish
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.rist.ritasax.ui.screens.EmailScreen
import com.rist.ritasax.ui.screens.FileReceiveScreen
import com.rist.ritasax.ui.screens.ProcessScreen
import com.rist.ritasax.ui.screens.RequestScreen
import com.rist.ritasax.ui.screens.UploadScreen
import com.rist.ritasax.ui.viewmodel.AppViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RitasAxApp(viewModel: AppViewModel = viewModel()) {
    val navController = rememberNavController()
    val state by viewModel.uiState.collectAsState()
    val backStack by navController.currentBackStackEntryAsState()

    Scaffold(
        topBar = {
            CenterAlignedTopAppBar(
                title = { Text("RITAS AX") },
                actions = {
                    IconButton(onClick = { viewModel.exitApp() }) {
                        Icon(Icons.Default.Close, contentDescription = "앱 종료")
                    }
                }
            )
        },
        bottomBar = {
            NavigationBar {
                AppDestination.items.forEach { item ->
                    val icon = when (item) {
                        AppDestination.FileReceive -> Icons.Default.FileDownload
                        AppDestination.Request -> Icons.Default.ListAlt
                        AppDestination.Process -> Icons.Default.SmartToy
                        AppDestination.Upload -> Icons.Default.Publish
                        AppDestination.Email -> Icons.Default.Email
                    }
                    NavigationBarItem(
                        selected = backStack?.destination?.route == item.route,
                        onClick = { navController.navigate(item.route) },
                        icon = { Icon(icon, contentDescription = item.title) },
                        label = { Text(item.title) },
                    )
                }
            }
        }
    ) { innerPadding ->
        NavHost(navController, startDestination = AppDestination.FileReceive.route, modifier = Modifier.padding(innerPadding)) {
            composable(AppDestination.FileReceive.route) { FileReceiveScreen(state, viewModel) }
            composable(AppDestination.Request.route) { RequestScreen(state, viewModel) }
            composable(AppDestination.Process.route) { ProcessScreen(state, viewModel) }
            composable(AppDestination.Upload.route) { UploadScreen(state, viewModel) }
            composable(AppDestination.Email.route) { EmailScreen(state, viewModel) }
        }
    }
}
