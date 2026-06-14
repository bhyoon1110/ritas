#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: D12 라이브러리 DB 파일의 각 4096바이트 블록을 임시 파일로 저장해
#            brukeropusreader로 직접 읽기를 시도한다. (콘솔 출력, brukeropusreader 필요)
# 실행 방법: python scripts/opus_parsing/try_opus_reader.py
#            (인자 없음 — D12 경로는 상단 LIB_DIR 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""brukeropusreader로 D12 파일 직접 읽기 시도"""
from brukeropusreader import read_file
import numpy as np

LIB_DIR = "data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/"

# D12는 OPUS 단일 스펙트럼 파일이 아니라 라이브러리 DB 파일
# 하지만 각 4096-byte 블록이 개별 OPUS 파일처럼 저장됐을 수 있음
# 블록을 임시 파일로 저장하고 읽어보기

import tempfile, os, struct

D12 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D12"

print("방법 1: D12 블록 1을 임시 파일로 저장하고 brukeropusreader로 읽기")
with open(D12, 'rb') as f:
    f.seek(4096)
    blk1 = f.read(4096)

with tempfile.NamedTemporaryFile(suffix='.0', delete=False) as tmp:
    tmp.write(blk1)
    tmp_path = tmp.name

try:
    data = read_file(tmp_path)
    print(f"성공! keys: {list(data.keys())}")
    for k in data.keys():
        v = data[k]
        if hasattr(v, '__len__') and len(v) > 10:
            print(f"  {k}: 길이={len(v)}, 처음 5개={list(v[:5])}")
        else:
            print(f"  {k}: {v}")
except Exception as e:
    print(f"실패: {e}")
finally:
    os.unlink(tmp_path)

print()
print("방법 2: D01 블록 1을 읽기")
D01 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D01"
with open(D01, 'rb') as f:
    f.seek(4096)
    blk1 = f.read(4096)

with tempfile.NamedTemporaryFile(suffix='.0', delete=False) as tmp:
    tmp.write(blk1)
    tmp_path = tmp.name

try:
    data = read_file(tmp_path)
    print(f"성공! keys: {list(data.keys())}")
except Exception as e:
    print(f"실패: {e}")
finally:
    os.unlink(tmp_path)

print()
print("방법 3: D01 블록 1 + 이후 4개 블록 연결하여 읽기")
with open(D01, 'rb') as f:
    f.seek(4096)
    blk_all = f.read(4096 * 7)

with tempfile.NamedTemporaryFile(suffix='.0', delete=False) as tmp:
    tmp.write(blk_all)
    tmp_path = tmp.name

try:
    data = read_file(tmp_path)
    print(f"성공! keys: {list(data.keys())}")
except Exception as e:
    print(f"실패: {e}")
finally:
    os.unlink(tmp_path)

print()
print("방법 4: brukeropusreader 소스 코드로 파싱 방식 확인")
import inspect
from brukeropusreader import opus_reader
src = inspect.getsource(opus_reader)
# magic number 찾기
import re
magic_lines = [l for l in src.split('\n') if 'magic' in l.lower() or '0x0A' in l or 'struct' in l.lower()][:20]
for l in magic_lines:
    print(" ", l)
