#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: S01 파일에서 인쇄 가능 문자열(4자 이상)을 정규식으로 추출해
#            화합물명 등 텍스트 패턴을 출력한다. (콘솔 출력)
# 실행 방법: python scripts/opus_parsing/read_s01.py
#            (인자 없음 — S01 경로는 상단 LIB_DIR 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
import struct, re

LIB_DIR = "data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/"

# S01 파일에서 텍스트 추출
with open(LIB_DIR + "ATR-FTIR O-RING LIBRARY.S01", 'rb') as f:
    s01 = f.read()

printable = re.findall(b'[ -~]{4,}', s01)
print(f"S01 크기: {len(s01)} bytes")
print("텍스트 패턴:")
for p in printable[:80]:
    txt = p.decode('latin-1')
    print("  " + repr(txt))
