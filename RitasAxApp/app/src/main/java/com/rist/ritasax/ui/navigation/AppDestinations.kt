package com.rist.ritasax.ui.navigation

sealed class AppDestination(val route: String, val title: String) {
    data object FileReceive : AppDestination("file_receive", "① 파일 수신")
    data object Request : AppDestination("request", "② 의뢰번호 조회")
    data object Process : AppDestination("process", "③ 전처리 + 보고서")
    data object Upload : AppDestination("upload", "④ LIMS 업로드")
    data object Email : AppDestination("email", "⑤ 메일 발송")

    companion object {
        val items = listOf(FileReceive, Request, Process, Upload, Email)
    }
}
