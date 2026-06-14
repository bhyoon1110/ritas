package com.rist.ritasax

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.material3.Surface
import com.rist.ritasax.ui.navigation.RitasAxApp
import com.rist.ritasax.ui.theme.RitasAxTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            RitasAxTheme {
                Surface {
                    RitasAxApp()
                }
            }
        }
    }
}
