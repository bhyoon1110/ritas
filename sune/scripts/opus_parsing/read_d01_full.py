#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: D01의 8개 블록을 하나의 파일처럼 조합해 brukeropusreader로 전체
#            스펙트럼 재구성을 시도한다. (콘솔 출력, brukeropusreader 필요)
# 실행 방법: python scripts/opus_parsing/read_d01_full.py
#            (인자 없음 — D01/D12 경로는 상단 LIB_DIR 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""D01 모든 블록을 조합하여 전체 스펙트럼 파일 재구성 시도"""
from brukeropusreader import read_file
import numpy as np, tempfile, os

LIB_DIR = "data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/"
D01 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D01"
D12 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D12"

# D01 전체(8개 블록)를 하나의 파일처럼 읽기
with open(D01, 'rb') as f:
    full_d01 = f.read()

print("D01 전체를 brukeropusreader로 읽기 (전체 파일):")
with tempfile.NamedTemporaryFile(suffix='.0', delete=False) as tmp:
    tmp.write(full_d01)
    tmp_path = tmp.name

try:
    data = read_file(tmp_path)
    print(f"keys: {list(data.keys())}")
    for k, v in data.items():
        if hasattr(v, '__len__') and len(v) > 5:
            print(f"  {k}: 길이={len(v)}, min={min(v):.4f}, max={max(v):.4f}, 처음 5개={list(v[:5])}")
        else:
            print(f"  {k}: {v}")
except Exception as e:
    print(f"실패: {e}")
finally:
    os.unlink(tmp_path)

print()
print("=" * 60)
print("D01 블록 0만 (헤더 블록):")
with tempfile.NamedTemporaryFile(suffix='.0', delete=False) as tmp:
    tmp.write(full_d01[:4096])
    tmp_path = tmp.name
try:
    data = read_file(tmp_path)
    print(f"keys: {list(data.keys())}")
    for k, v in data.items():
        print(f"  {k}: {v}")
except Exception as e:
    print(f"실패: {e}")
finally:
    os.unlink(tmp_path)

print()
print("=" * 60)
print("brukeropusreader 내부 파싱 코드 분석:")
from brukeropusreader import opus_reader
import inspect
src = inspect.getsource(opus_reader)
print(src[:3000])
