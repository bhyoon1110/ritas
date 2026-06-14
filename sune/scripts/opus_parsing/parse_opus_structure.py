#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS 블록 헤더(3 x uint32: 데이터 타입·채널·레코드 번호) 포맷으로
#            D12 라이브러리 블록 파싱을 시도하고 그래프(PNG)를 저장한다. (디버그용)
# 실행 방법: python scripts/opus_parsing/parse_opus_structure.py
#            (인자 없음 — 경로는 상단 LIB_DIR 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
OPUS 파일 포맷으로 D12 라이브러리 블록 파싱 시도
OPUS 파일은 블록 헤더 (3 x uint32)로 시작하며 데이터 타입, 채널, 레코드 번호를 포함함
"""
import struct
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

LIB_DIR = "data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/"

def read_opus_block_from_file(fname, block_offset):
    """OPUS 포맷으로 블록 파싱"""
    with open(fname, 'rb') as f:
        f.seek(block_offset)
        raw = f.read(4096)
    return raw

def parse_opus_header(data, verbose=True):
    """OPUS 파일 헤더 파싱: 24 bytes의 파일 헤더 + 블록 디렉토리"""
    # OPUS 파일 포맷:
    # offset 0: magic (4 bytes) - typically 0x0A, 0x00, 0x00, 0x00 or similar
    # offset 4: first dir entry offset
    # offset 8: max block count
    # offset 12: actual block count
    # Then block directory: entries of 12 bytes each
    #   [block_type (4), data_length (4), data_offset (4)]
    
    if verbose:
        print("헤더 분석 (처음 64 바이트):")
        for i in range(0, min(64, len(data)), 16):
            hex_p = ' '.join(f'{b:02x}' for b in data[i:i+16])
            asc = ''.join(chr(b) if 32<=b<127 else '.' for b in data[i:i+16])
            print(f"  0x{i:04X}: {hex_p}  {asc}")
    
    return None

D12 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D12"

print("=" * 60)
print("D12 블록 1을 OPUS 포맷으로 파싱")
print("=" * 60)
blk1 = read_opus_block_from_file(D12, 4096)

# OPUS 파일의 블록 디렉토리 구조를 이용해 파싱
# 각 블록은 12 바이트 디렉토리 엔트리:
# [block_type_id(uint32), block_size(uint32), block_abs_offset(uint32)]
# 파일 헤더는 24 bytes

# 블록 내부에서 OPUS 디렉토리 찾기
print("\n8바이트 단위로 uint32 값 읽기 (처음 100개):")
for i in range(0, min(400, len(blk1)), 4):
    v = struct.unpack_from('<I', blk1, i)[0]
    if 4096 < v < 1150976:  # D12 파일 크기 범위 내 오프셋
        print(f"  offset 0x{i:04X}: {v} (0x{v:08X}) <- 유효 파일 오프셋?")

print()
print("=" * 60)
print("D12 블록 1 offset 12부터 12-byte 구조로 읽기 (OPUS 블록 디렉토리?)")
print("=" * 60)
# Raima 페이지 헤더 이후 OPUS 블록 디렉토리 시작 시도
for hdr_size in [8, 12, 16, 20]:
    print(f"\nhdr_size={hdr_size}:")
    for i in range(0, 60):
        off = hdr_size + i * 12
        if off + 12 > len(blk1):
            break
        block_type, block_size, block_offset = struct.unpack_from('<III', blk1, off)
        if block_type != 0 and block_size > 0 and block_size < 100000:
            print(f"  [{i}] type=0x{block_type:08X} size={block_size:6d} offset={block_offset:8d}")

print()
print("=" * 60)
print("D12 블록 내 OPUS 데이터 블록 타입 ID 탐색")
print("=" * 60)
# 알려진 OPUS 블록 타입 (Data Block Codes from OPUS documentation)
# AB = 0x0F (Absorbance)
# TR = 0x02 (Transmittance)
# SP = 0x0A (Single channel reference)
# SB = 0x09 (Single channel background)
# Data blocks have type = code + channel * 256 + data_set_code * 65536

known_types = {
    0x0F: 'AB (Absorbance)',
    0x02: 'TR (Transmittance)',
    0x0A: 'SP (Single channel)',
    0x09: 'SB (Background)',
    0x0B: 'Data Parameters AB',
    0x0C: 'Data Parameters TR',
}

# 파일 전체에서 1:AB 패턴에 해당하는 데이터를 찾기
# OPUS 내부에서 AB 블록 타입은 일반적으로 0x0F 또는 (0x0F | 0x100 | 0x200) 형태
with open(D12, 'rb') as f:
    all_bytes = f.read()

# 4096바이트 블록별로 OPUS 헤더 탐색
print("각 블록의 첫 12 bytes를 OPUS 파일 헤더로 해석:")
# OPUS magic word 확인: 0x00, 0x00, 0x00, 0x0A (little endian: 0x0A000000)
for b_idx in range(0, min(10, 281)):
    off = b_idx * 4096
    magic = struct.unpack_from('<I', all_bytes, off)[0]
    dir_off = struct.unpack_from('<I', all_bytes, off+4)[0] if off+8 <= len(all_bytes) else 0
    max_blk = struct.unpack_from('<H', all_bytes, off+8)[0] if off+10 <= len(all_bytes) else 0
    n_blk   = struct.unpack_from('<H', all_bytes, off+10)[0] if off+12 <= len(all_bytes) else 0
    print(f"  Block {b_idx}: magic=0x{magic:08X} dir_off={dir_off} max_blk={max_blk} n_blk={n_blk}")

print()
print("=" * 60) 
print("Raima 4.5 Build 17 페이지 구조 조사")
print("페이지 헤더: 8 bytes (4 = timestamp, 4 = page_num)")
print("실제 데이터 시작: offset 8")
print("=" * 60)

# D12 블록 1의 offset 8부터 다양한 sub-structure 해석
blk = all_bytes[4096:8192]  # block 1

# Raima 4.5 페이지 레이아웃:
# bytes 0-3: last modified timestamp
# bytes 4-7: page number / sequence
# bytes 8-11: overflow/next page
# bytes 12+: actual record data
# 각 레코드는 레코드 크기로 구분됨

# D12에서 1005 포인트 레코드 크기 계산:
# 1005 * 4 = 4020 bytes (float32)
# 4096 - 8 header = 4088 가용 바이트
# 4088 / 4020 = 1.017... → 1개 레코드만 들어감
# 남는 공간: 4088 - 4020 = 68 bytes

print(f"블록 크기: 4096")
print(f"헤더: 8 bytes")
print(f"가용: {4096-8} bytes")
print(f"1005 float32 = {1005*4} bytes")
print(f"남는 공간: {4096-8-1005*4} bytes")
print()

# 따라서 레코드 구조: [8바이트 페이지헤더] + [레코드] 
# 레코드 내 오프셋 계산:
# [레코드 헤더] + [4020 bytes 스펙트럼] + [패딩]
# 레코드 헤더 크기 추정: 4096 - 8 - 4020 = 68 bytes

# offset 8+68 = 76에서 시작?
test_arr = np.frombuffer(blk[76:76+4020], dtype=np.float32)
print(f"offset=76 float32: min={test_arr.min():.4f} max={test_arr.max():.4f}")
print(f"  neg={np.sum(test_arr<0)}, large={np.sum(test_arr>2)}, nan={np.sum(~np.isfinite(test_arr))}")

# S01에서 발견: 데이터 타입 1:AB -> OPUS AB 블록
# OPUS에서 AB 블록은 float32 배열
# 메타데이터 블록이 있고 스펙트럼 블록이 별도

# D01 8개 스펙트럼 → 8개 블록
# D01에서 실제 스펙트럼 블록 조사
print()
print("=" * 60)
print("D01 블록 1 분석 (1st spectrum)")
print("D01 = 8 spectra, block size = 4096")
print("=" * 60)
D01 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D01"
with open(D01, 'rb') as f:
    d01_all = f.read()

# D01 블록 1
d01_blk1 = d01_all[4096:8192]
print("D01 Block 1 전체:")
for i in range(0, 128, 16):
    hex_p = ' '.join(f'{b:02x}' for b in d01_blk1[i:i+16])
    asc = ''.join(chr(b) if 32<=b<127 else '.' for b in d01_blk1[i:i+16])
    print(f"  0x{4096+i:04X}: {hex_p}  {asc}")

print()
# D01 블록 1의 나머지 (중간 부분에서 0값이 어디서 시작?)
# 0 패딩 시작 위치 찾기
non_zero_end = 4095
for i in range(4095, -1, -1):
    if d01_blk1[i] != 0:
        non_zero_end = i
        break
print(f"D01 블록1: 마지막 non-zero byte at offset {non_zero_end} (0x{non_zero_end+4096:04X})")
print(f"실제 데이터 크기: {non_zero_end+1} bytes")

# D01 블록 1의 non-zero 부분 마지막 64 bytes
if non_zero_end > 64:
    print("D01 블록1 마지막 64 non-zero bytes:")
    start_show = max(0, non_zero_end - 63)
    for i in range(start_show, non_zero_end+1, 16):
        chunk = d01_blk1[i:min(i+16, non_zero_end+1)]
        hex_p = ' '.join(f'{b:02x}' for b in chunk)
        asc = ''.join(chr(b) if 32<=b<127 else '.' for b in chunk)
        print(f"  0x{4096+i:04X}: {hex_p}  {asc}")
