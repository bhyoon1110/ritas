"""PowerPoint password-to-modify (not open-file encryption).

OOXML p:modifyVerifier, SHA-512 / 100,000 spins. The write-protection hash
appends the little-endian counter, unlike an Office encryption password.
See Apache POI CryptoFunctions.hashPassword(..., iteratorFirst=false).
Only salt and verifier are persisted; authoring PPTs remain unprotected.
"""
import base64
import hashlib
import io
import json
import secrets
import threading
import zipfile
from urllib.parse import urlsplit

from lxml import etree
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..errors import ApiException

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
SPINS = 100_000
_SLOTS = threading.BoundedSemaphore(2)
_FIXED = dict(cryptProviderType="rsaAES", cryptAlgorithmClass="hash",
              cryptAlgorithmType="typeAny", cryptAlgorithmSid="14", spinCount=str(SPINS))


def validate_protection(value):
    if value is None or value == {}:
        return {}
    try:
        if not isinstance(value, dict) or set(value) != {*_FIXED, "saltData", "hashData"}:
            raise ValueError
        if any(value[k] != v for k, v in _FIXED.items()):
            raise ValueError
        for key, size in (("saltData", 16), ("hashData", 64)):
            text = value[key]
            if not isinstance(text, str) or len(text) > 88:
                raise ValueError
            raw = base64.b64decode(text, validate=True)
            if len(raw) != size or base64.b64encode(raw).decode("ascii") != text:
                raise ValueError
    except (ValueError, TypeError):
        raise ValueError("PPT 수정 비밀번호 설정이 올바르지 않습니다. 다시 설정하세요.") from None
    return dict(value)


def create_protection(password):
    # Do not silently truncate or normalize a user's password.
    if not isinstance(password, str) or not password or password.startswith("\ufeff"):
        raise ValueError("비밀번호는 1~255자이며 첫 글자로 BOM을 사용할 수 없습니다.")
    try:
        encoded = password.encode("utf-16le")
    except UnicodeError:
        raise ValueError("비밀번호에 사용할 수 없는 문자가 있습니다.") from None
    if len(encoded) > 510:
        raise ValueError("비밀번호는 UTF-16 기준 255자 이하로 입력하세요.")
    salt = secrets.token_bytes(16)
    digest = hashlib.sha512(salt + encoded).digest()
    for i in range(SPINS):
        digest = hashlib.sha512(digest + i.to_bytes(4, "little")).digest()
    return dict(_FIXED, saltData=base64.b64encode(salt).decode("ascii"),
                hashData=base64.b64encode(digest).decode("ascii"))


def protect_pptx(content, protection):
    """Return a delivery copy; preserve hyperlinks and every other OPC part."""
    protection = validate_protection(protection)
    if not protection:
        return content
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        root = etree.fromstring(source.read("ppt/presentation.xml"),
                                etree.XMLParser(resolve_entities=False, no_network=True))
        for old in root.findall(f"{{{P}}}modifyVerifier"):
            root.remove(old)
        node = etree.Element(f"{{{P}}}modifyVerifier", **protection)
        # CT_Presentation: defaultTextStyle, modifyVerifier, extLst.
        extension = root.find(f"{{{P}}}extLst")
        if extension is None:
            root.append(node)
        else:
            extension.addprevious(node)
        for item in source.infolist():
            data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True) if item.filename == "ppt/presentation.xml" else source.read(item)
            target.writestr(item, data)
    return output.getvalue()


async def password_verifier_response(request):
    """Called only after the report's existing author/project authorization.

    This endpoint creates no saved setting. Existing revision/save APIs persist
    the verifier together with the report, with their usual conflict checks.
    """
    origin = request.headers.get("origin")
    if request.headers.get("x-requested-with") != "RIST-Report-Editor" or (origin and
            (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc)):
        raise ApiException(403, "REPORT_EDIT_ORIGIN_INVALID", "같은 사이트의 보고서 편집 화면에서 설정하세요.")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 4096:
            raise ApiException(413, "PPT_PASSWORD_TOO_LARGE", "비밀번호는 255자 이하로 입력하세요.")
    try:
        value = json.loads(content)
        if not isinstance(value, dict) or set(value) != {"password"}:
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise ApiException(422, "PPT_PASSWORD_INVALID", "비밀번호 입력을 확인하세요.") from None
    if not _SLOTS.acquire(blocking=False):
        raise ApiException(429, "PPT_PASSWORD_BUSY", "잠시 후 비밀번호 설정을 다시 시도하세요.")
    try:
        try:
            protection = await run_in_threadpool(create_protection, value["password"])
        except ValueError as exc:
            raise ApiException(422, "PPT_PASSWORD_INVALID", str(exc)) from None
        return JSONResponse({"protection": protection}, headers={"Cache-Control": "no-store"})
    finally:
        _SLOTS.release()
        content.clear()
        value.clear()
