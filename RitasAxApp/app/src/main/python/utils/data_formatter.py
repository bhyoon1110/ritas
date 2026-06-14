import json


def llm_payload(test_type, request_id, summary, details, extra_instruction):
    return {
        "test_type": test_type,
        "request_id": request_id,
        "summary": summary,
        "details": details,
        "instruction": extra_instruction,
    }


def top_n_records(records, n=10):
    return records[:n]
