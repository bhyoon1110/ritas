import json

from processors.tem import process as process_tem
from processors.gdms import process as process_gdms
from processors.xrd import process as process_xrd
from processors.ftir import process as process_ftir
from processors.xps import process as process_xps

PROCESSORS = {
    "TEM": process_tem,
    "GD-MS": process_gdms,
    "XRD": process_xrd,
    "FT-IR": process_ftir,
    "XPS": process_xps,
}


def process(test_type, request_id, file_paths_json, options_json="{}"):
    file_paths = json.loads(file_paths_json)
    options = json.loads(options_json or "{}")
    processor = PROCESSORS.get(test_type)
    if processor is None:
        raise ValueError("Unsupported test type: {}".format(test_type))
    result = processor(request_id=request_id, file_paths=file_paths, options=options)
    return json.dumps(result, ensure_ascii=False)
