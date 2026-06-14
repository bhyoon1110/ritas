from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel

from services.ppt_service import build_ppt

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

app = FastAPI(title="RITAS AX Edge Server")


class RequestInfoDto(BaseModel):
    request_id: str
    customer_name: str
    customer_email: str
    company: str
    test_type: str
    sample_names: List[str]
    status: str


class ReportRequestDto(BaseModel):
    request_id: str
    test_type: str
    llm_payload: str
    chart_paths: List[str] = []
    image_paths: List[str] = []
    summary: Dict[str, Any] = {}


class UploadRequestDto(BaseModel):
    request_id: str
    file_paths: List[str]


class EmailRequestDto(BaseModel):
    request_id: str
    message: str


@app.get('/api/lims/request/{request_id}')
def get_request_info(request_id: str):
    return RequestInfoDto(
        request_id=request_id,
        customer_name='홍길동',
        customer_email='hong@example.com',
        company='ABC Materials',
        test_type='TEM',
        sample_names=['Sample-1', 'Sample-2'],
        status='in_progress',
    )


@app.post('/api/process')
def generate_report(request: ReportRequestDto):
    report_id = str(uuid4())
    ppt_path = build_ppt(
        output_dir=OUTPUT_DIR,
        report_id=report_id,
        request_id=request.request_id,
        test_type=request.test_type,
        summary=request.summary,
        chart_paths=request.chart_paths,
        image_paths=request.image_paths,
        llm_payload=request.llm_payload,
    )
    return {
        'report_id': report_id,
        'summary': f"{request.test_type} 보고서가 생성되었습니다.",
        'ppt_url': str(ppt_path),
        'attachment_urls': [str(p) for p in request.chart_paths + request.image_paths],
    }


@app.post('/api/lims/upload-local')
def upload_local(request: UploadRequestDto):
    return {'success': True, 'message': f'LIMS 업로드 완료: {len(request.file_paths)}개 파일'}


@app.post('/api/lims/send-email')
def send_email(request: EmailRequestDto):
    return {'success': True, 'message': f'{request.request_id} 의뢰에 대한 메일 발송 완료'}
