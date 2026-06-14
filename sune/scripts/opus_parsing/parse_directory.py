#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: D01·D12 블록에서 OPUS 블록 디렉토리(데이터 타입 ID 등)를 파싱한다.
#            (콘솔 출력)
# 실행 방법: python scripts/opus_parsing/parse_directory.py
#            (인자 없음 — D01/D12 경로는 상단 LIB_DIR 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""D01/D12 블록에서 OPUS 블록 디렉토리 파싱"""
import struct
import numpy as np

LIB_DIR = "data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/"
D01 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D01"
D12 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D12"

# 알려진 OPUS data type IDs
DATA_TYPES = {
    0x0: 'Undefined',
    0x1: 'Real Part',
    0x2: 'Imaginary Part',
    0x3: 'Single channel',
    0x5: 'Phase',
    0x6: 'Power (Abs Sq)',
    0x7: 'Log of SC intensity',
    0xF: 'Absorbance',
    0x11: 'Kubelka Munk',
    0x13: 'Transmittance',
    0x14: 'Reflectance',
    0x16: 'ATR with correction',
    0x17: 'Photoacoustic',
    0x1F: 'Emission intensity',
    0x22: 'Raman intensity',
    0x64: 'Data Point Table',
    0x0B: 'Data Status Parameters',
    0x0C: 'Instrument Parameters',
    0x31: 'Reference/Background Instrument Parameters',
    0x33: 'Optik Parameters',
    0x35: 'Acquisition Parameters',
    0x37: 'Sample Origin Parameters',
    0x40: 'Software Parameters',
    0x48: 'History',
    0x59: 'Compound Class',
}

def parse_opus_directory(data, base_offset=0):
    """OPUS 블록 디렉토리 파싱 (offset 24에서 시작, 12 bytes씩)"""
    HEADER_LEN = 504
    FIRST_CURSOR = 24
    META_BLOCK_SIZE = 12
    
    entries = []
    cursor = FIRST_CURSOR
    while cursor + META_BLOCK_SIZE <= min(HEADER_LEN, len(data)):
        data_type = struct.unpack_from('<B', data, cursor)[0]
        channel_type = struct.unpack_from('<B', data, cursor+1)[0]
        text_type = struct.unpack_from('<B', data, cursor+2)[0]
        chunk_size = struct.unpack_from('<I', data, cursor+4)[0]
        offset = struct.unpack_from('<I', data, cursor+8)[0]
        
        if offset <= 0:
            break
        
        type_name = DATA_TYPES.get(data_type, f'Unknown(0x{data_type:02X})')
        entries.append({
            'data_type': data_type, 'channel_type': channel_type,
            'text_type': text_type, 'chunk_size': chunk_size, 'offset': offset,
            'type_name': type_name
        })
        
        cursor += META_BLOCK_SIZE
    
    return entries

print("=" * 70)
print("D01 블록 1 (offset=4096)을 OPUS 파일로 파싱")
print("=" * 70)
with open(D01, 'rb') as f:
    f.seek(4096)
    d01_blk1 = f.read(4096)

entries = parse_opus_directory(d01_blk1)
print(f"발견된 블록 수: {len(entries)}")
for e in entries:
    print(f"  type=0x{e['data_type']:02X}({e['type_name']}) "
          f"ch={e['channel_type']} txt={e['text_type']} "
          f"size={e['chunk_size']} offset={e['offset']}")
    if e['chunk_size'] > 0 and e['offset'] > 0:
        abs_off = 4096 + e['offset']
        abs_end = abs_off + e['chunk_size'] * 4
        print(f"       → file offset {abs_off} to {abs_end}")

print()
print("=" * 70)
print("D01 블록들 각각을 OPUS 파일로 파싱")
print("=" * 70)
with open(D01, 'rb') as f:
    d01_full = f.read()

# 각 4096-byte 블록을 독립 OPUS 파일로 시도
for blk_idx in range(0, 8):
    blk = d01_full[blk_idx*4096:(blk_idx+1)*4096]
    entries = parse_opus_directory(blk)
    if entries:
        print(f"\nBlock {blk_idx}: {len(entries)} entries")
        for e in entries:
            print(f"  type=0x{e['data_type']:02X}({e['type_name']}) "
                  f"size={e['chunk_size']} offset={e['offset']}")
            if e['data_type'] in [0x0F, 0x13, 0x03, 0x07] and e['chunk_size'] > 100:
                # 스펙트럼 데이터 읽기
                off = e['offset']
                sz = e['chunk_size']
                if off + sz*4 <= len(blk):
                    arr = np.frombuffer(blk[off:off+sz*4], dtype=np.float32)
                    print(f"    [스펙트럼!] min={arr.min():.4f} max={arr.max():.4f} "
                          f"neg={np.sum(arr<0)} pts={len(arr)}")

print()
print("=" * 70)
print("D12 블록 1을 OPUS 파일로 파싱")
print("=" * 70)
with open(D12, 'rb') as f:
    f.seek(4096)
    d12_blk1 = f.read(4096)

entries = parse_opus_directory(d12_blk1)
print(f"발견된 블록 수: {len(entries)}")
for e in entries:
    print(f"  type=0x{e['data_type']:02X}({e['type_name']}) "
          f"ch={e['channel_type']} txt={e['text_type']} "
          f"size={e['chunk_size']} offset={e['offset']}")
    if e['chunk_size'] > 0 and e['offset'] > 0 and e['offset'] < 4096:
        arr = np.frombuffer(d12_blk1[e['offset']:e['offset']+e['chunk_size']*4], dtype=np.float32)
        finite_arr = arr[np.isfinite(arr)]
        if len(finite_arr) > 0:
            in_range = np.sum((finite_arr >= -0.5) & (finite_arr <= 2.5))
            print(f"  → float32: min={arr.min():.4f} max={arr.max():.4f} "
                  f"neg={np.sum(arr<0)} in_range={in_range}")

print()
print("=" * 70)
print("D01 전체 파일을 하나의 연속된 OPUS 파일로 읽기")
print("즉, 헤더가 파일 블록 1 (offset 4096)에 있고")
print("실제 데이터가 다른 D 파일들에 있을 수 있음")
print("=" * 70)

# S01 로그: OPUS 파일들은 여러 D 파일에 분산 저장됨
# D01 → 텍스트/메타데이터 블록
# D12 → 실제 스펙트럼 데이터 (float32 블록)
# 스펙트럼 데이터가 D12에 있고, 참조 오프셋이 D01에 있을 수 있음

print("D01 블록1 데이터에서 D12 오프셋 탐색:")
with open(D01, 'rb') as f:
    f.seek(4096)
    d01_blk1 = f.read(4096)

# D12 파일 크기 = 1,150,976
# D01 블록에서 1150976 이하 값 찾기
for i in range(0, 4096-4, 4):
    v = struct.unpack_from('<I', d01_blk1, i)[0]
    if 4096 < v < 1150976 and v % 4096 == 0:
        print(f"  D01블록1 offset 0x{i:04X}: {v} (= D12 block {v//4096}?)")
