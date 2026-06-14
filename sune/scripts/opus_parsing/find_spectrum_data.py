#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: D12 블록을 여러 오프셋·데이터 타입으로 해석해 실제 스펙트럼
#            데이터를 찾고 결과 그래프(PNG)를 저장한다. (디버그용 matplotlib)
# 실행 방법: python scripts/opus_parsing/find_spectrum_data.py
#            (인자 없음 — D11/D12 경로는 상단 LIB_DIR 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""D12 블록 구조를 다양한 방법으로 분석하여 실제 스펙트럼 데이터 찾기"""
import struct
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

LIB_DIR = "data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/"
D12 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D12"
D11 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D11"

def hexdump(data, start=0, length=256, offset=0):
    for i in range(0, min(length, len(data)), 16):
        hex_part = ' '.join(f'{b:02x}' for b in data[i:i+16])
        asc_part = ''.join(chr(b) if 32<=b<127 else '.' for b in data[i:i+16])
        print(f"0x{offset+i:04X}: {hex_part:<47}  {asc_part}")

print("=" * 70)
print("D12 블록 1 전체 덤프 (처음 512 바이트)")
print("=" * 70)
with open(D12, 'rb') as f:
    f.seek(4096)
    block1 = f.read(4096)

hexdump(block1, length=512, offset=4096)

print()
print("=" * 70)
print("D12 블록 1 - 다양한 오프셋에서 float32 시도")
print("=" * 70)

# 각 오프셋에서 1005 float32 읽기 시도
for start_off in [8, 16, 24, 32, 40, 48, 52, 56, 64, 72, 76, 80, 88, 96, 100, 108, 112, 120]:
    end_off = start_off + 1005 * 4
    if end_off > 4096:
        break
    arr = np.frombuffer(block1[start_off:end_off], dtype=np.float32)
    in_range = np.sum((arr >= 0) & (arr <= 2))
    neg = np.sum(arr < 0)
    large = np.sum(arr > 2)
    finite = np.sum(np.isfinite(arr))
    print(f"offset={start_off:3d}: min={arr.min():.4f} max={arr.max():.4f} "
          f"neg={neg:4d} large={large:4d} in[0,2]={in_range:4d} finite={finite}")

print()
print("=" * 70)
print("D12 블록 1 - int16으로 읽기 시도 (다양한 오프셋)")
print("=" * 70)
for start_off in [8, 16, 24, 32, 40, 48, 56, 64]:
    arr_i = np.frombuffer(block1[start_off:start_off+2010], dtype=np.int16)
    arr_u = np.frombuffer(block1[start_off:start_off+2010], dtype=np.uint16)
    print(f"offset={start_off:3d} int16: min={arr_i.min():6d} max={arr_i.max():6d} "
          f"| uint16: min={arr_u.min():6d} max={arr_u.max():6d}")

print()
print("=" * 70)  
print("D12 블록 0 전체 덤프 (처음 256 바이트) - 파일 헤더 확인")
print("=" * 70)
with open(D12, 'rb') as f:
    block0 = f.read(4096)
hexdump(block0, length=256, offset=0)

print()
print("=" * 70)
print("D12 블록 2, 3 (처음 64 바이트씩) - 패턴 확인")
print("=" * 70)
with open(D12, 'rb') as f:
    for i in [2, 3, 5, 10]:
        f.seek(i * 4096)
        blk = f.read(64)
        print(f"블록 {i}:")
        hexdump(blk, length=64, offset=i*4096)

print()
print("=" * 70)
print("ARTs D11 파일 분석 (1018바이트 레코드 타입)")
print("=" * 70)
try:
    with open("data/Library/ARTs/ART.D11", 'rb') as f:
        d11_data = f.read()
    print(f"ART.D11 크기: {len(d11_data)} 바이트")
    hexdump(d11_data, length=256, offset=0)
    
    # D11을 1018바이트 레코드로 파싱 시도
    page_size = 4096
    page_header = 8
    record_size = 1018
    records_per_page = (page_size - page_header) // record_size
    print(f"\n페이지당 레코드 수: {records_per_page}")
    
    # 첫 번째 레코드의 데이터 시도
    for skip_bytes in [8, 12, 16, 24, 32]:
        rec_data = d11_data[page_header + skip_bytes: page_header + record_size]
        n_floats = len(rec_data) // 4
        arr = np.frombuffer(rec_data[:n_floats*4], dtype=np.float32)
        neg = np.sum(arr < 0)
        large = np.sum(arr > 2)
        finite = np.sum(np.isfinite(arr))
        if finite > 100:
            print(f"skip={skip_bytes}: n={n_floats} min={arr.min():.4f} max={arr.max():.4f} neg={neg} large={large}")
except FileNotFoundError:
    print("ART.D11 없음")

print()
print("=" * 70)
print("O-RING D12 블록 전체를 uint16으로 읽어 히스토그램")
print("=" * 70)
with open(D12, 'rb') as f:
    # 블록 1만 uint16으로 읽기
    f.seek(4096)
    blk = f.read(4096)
arr_u16 = np.frombuffer(blk, dtype=np.uint16)
print(f"uint16 값 분포: min={arr_u16.min()} max={arr_u16.max()}")
print(f"0 값 비율: {np.sum(arr_u16==0)/len(arr_u16)*100:.1f}%")
print(f"256 이하 비율: {np.sum(arr_u16<=256)/len(arr_u16)*100:.1f}%")
print(f"1000 이하 비율: {np.sum(arr_u16<=1000)/len(arr_u16)*100:.1f}%")

# 히스토그램 플롯
fig, axes = plt.subplots(2, 2, figsize=(14, 10))

# 블록 1 전체를 float32로
with open(D12, 'rb') as f:
    f.seek(4096 + 8)
    raw = f.read(4096 - 8)
arr_f32 = np.frombuffer(raw[:len(raw)//4*4], dtype=np.float32)
arr_f32_finite = arr_f32[np.isfinite(arr_f32)]
arr_f32_range = arr_f32_finite[(arr_f32_finite >= -0.5) & (arr_f32_finite <= 2.5)]

axes[0,0].plot(arr_f32_finite[:500])
axes[0,0].set_title('D12 블록1 (offset 8) float32 처음 500개')
axes[0,0].set_ylabel('value')

# 각 블록의 첫 번째 float32 값 (offset 8)
first_vals = []
with open(D12, 'rb') as f:
    for b in range(0, 280):
        f.seek(b * 4096 + 8)
        v = struct.unpack('<f', f.read(4))[0]
        first_vals.append(v if abs(v) < 1e10 else 0)
axes[0,1].plot(first_vals)
axes[0,1].set_title('각 블록 offset 8의 float32 값 (280 블록)')

# uint16 히스토그램
axes[1,0].hist(arr_u16, bins=256)
axes[1,0].set_title('D12 블록1 uint16 히스토그램')
axes[1,0].set_yscale('log')

# D12 블록 5를 offset 52에서 float32로
with open(D12, 'rb') as f:
    f.seek(5 * 4096 + 52)
    test_data = np.frombuffer(f.read(1005*4), dtype=np.float32)
axes[1,1].plot(test_data)
axes[1,1].set_title(f'D12 블록5 offset=52 float32 (min={test_data.min():.3f})')

plt.tight_layout()
plt.savefig('find_spectrum_debug.png', dpi=100)
print("그래프 저장: find_spectrum_debug.png")
