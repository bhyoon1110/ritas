#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: Raima 4.5 페이지 구조 가정으로 D11·D13 블록에서 스펙트럼
#            데이터 추출을 시도하고 결과를 d11_d13_spectra.png 로 저장한다. (디버그용)
# 실행 방법: python scripts/opus_parsing/extract_d11_d13.py
#            (인자 없음 — D11/D13 경로는 상단 LIB_DIR 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""D11과 D13에서 스펙트럼 데이터 추출 시도 (Raima 4.5 페이지 구조)"""
import struct
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

LIB_DIR = "data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/"
D11 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D11"
D13 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D13"

# D11: 1 spectrum, 4096 bytes = 1 block
# D13: 27 spectra, 110592 bytes = 27 blocks

# Raima 4.5 페이지 헤더 구조 (8 bytes):
# bytes 0-3: page timestamp
# bytes 4-7: page number/type info

# 레코드 헤더 구조 추정 (Raima 4.5):
# [2-byte record_length] [2-byte record_type] + data

# D11: 4096 bytes, 1 spectrum
# 헤더 8 bytes 이후 데이터 시작
# 레코드 크기 = 1018 bytes (ARTs DBD 기준)
# 4096 - 8 = 4088 bytes 가용 → 1018 * 4 = 4072 bytes (4개 레코드) vs 4020 (1005 floats)

with open(D11, 'rb') as f:
    d11 = f.read()

print("D11 (1 스펙트럼, 4096 bytes):")
print("처음 64 bytes:")
for i in range(0, 64, 16):
    h = ' '.join(f'{b:02x}' for b in d11[i:i+16])
    a = ''.join(chr(b) if 32<=b<127 else '.' for b in d11[i:i+16])
    print(f"  0x{i:04X}: {h}  {a}")

# D11에서 0이 아닌 데이터 범위 찾기
non_zero = [i for i in range(len(d11)-1, -1, -1) if d11[i] != 0]
if non_zero:
    last_nz = non_zero[0]
    print(f"\n마지막 비제로 byte: offset {last_nz}")
    print(f"실제 데이터 크기: {last_nz+1} bytes")
    print(f"데이터 부분 (헤더 8 bytes 제외): {last_nz+1-8} bytes")
    print(f"float32 수: {(last_nz+1-8)//4}")
    
    # 데이터 영역 헥스
    start = 4096 - 64
    print(f"\n마지막 64 bytes (offset {start}):")
    for i in range(start, 4096, 16):
        h = ' '.join(f'{b:02x}' for b in d11[i:i+16])
        a = ''.join(chr(b) if 32<=b<127 else '.' for b in d11[i:i+16])
        print(f"  0x{i:04X}: {h}  {a}")

print()

# D13 각 블록 분석
with open(D13, 'rb') as f:
    d13 = f.read()

print(f"D13 ({len(d13)//4096} blocks):")
print("\n각 블록의 비제로 범위:")
for blk_idx in range(min(5, len(d13)//4096)):
    blk = d13[blk_idx*4096:(blk_idx+1)*4096]
    non_zero = [i for i in range(len(blk)-1, -1, -1) if blk[i] != 0]
    if non_zero:
        last_nz = non_zero[0]
        data_size = last_nz + 1 - 8
        print(f"  블록 {blk_idx}: 마지막 비제로={last_nz}, 데이터={data_size} bytes, floats={data_size//4}")

# D13 블록 1 구조 자세히
blk = d13[4096:8192]
print("\nD13 블록 1 - Raima 레코드 구조 분석:")

# Raima 4.5 페이지에서 레코드들의 위치를 찾는 방법:
# 레코드들은 보통 페이지 내에서 연속으로 저장됨
# 각 레코드 앞에 작은 헤더가 있을 수 있음

# D13 블록 1의 반복 구조 분석
# 116 bytes 간격에 0x00000400 값이 있음을 이미 확인
# 116 bytes = 1개 레코드?

print("\n116 bytes 간격으로 레코드 헤더 시도:")
for rec_idx in range(35):
    off = 8 + rec_idx * 116  # 8 bytes 페이지 헤더 이후
    if off + 116 > 4096:
        break
    
    h = struct.unpack_from('<H', blk, off)[0]
    h2 = struct.unpack_from('<H', blk, off+2)[0]
    h3 = struct.unpack_from('<I', blk, off+4)[0]
    h4 = struct.unpack_from('<I', blk, off+8)[0]
    
    if rec_idx < 5:
        hex_16 = ' '.join(f'{b:02x}' for b in blk[off:off+16])
        print(f"  [rec {rec_idx}] off={off}: {hex_16}")

# float32로 읽기 시도 (다양한 헤더 크기 가정)
print("\nD13 블록 1에서 float32 읽기 (rec_size=116, 헤더 크기 가변):")
for hdr in [4, 8, 12, 16, 20, 24]:
    # 각 레코드에서 hdr bytes 건너뛰고 float32 읽기
    all_floats = []
    valid = True
    for rec_idx in range(35):
        off = 8 + rec_idx * 116 + hdr  # 페이지헤더 + 레코드위치 + 레코드헤더
        data_size = 116 - hdr
        n_floats = data_size // 4
        if off + n_floats*4 > 4096:
            break
        arr = np.frombuffer(blk[off:off+n_floats*4], dtype=np.float32)
        if np.any(np.abs(arr) > 1000) or np.any(~np.isfinite(arr)):
            valid = False
        all_floats.extend(arr.tolist())
    
    if valid and len(all_floats) > 100:
        arr = np.array(all_floats)
        print(f"  hdr={hdr}: min={arr.min():.4f} max={arr.max():.4f} "
              f"neg={np.sum(arr<0)} n={len(arr)}")

# D13 전체에서 스펙트럼 1개 조합 시도
# 27 spectra → 블록 1~27에 1개씩
print("\nD13 각 블록 시도 (offset=8 기준):")
for blk_idx in range(1, 27):
    blk = d13[blk_idx*4096:(blk_idx+1)*4096]
    
    # 블록 내 마지막 비제로 위치
    nz = [i for i in range(4095, 7, -1) if blk[i] != 0]
    if not nz:
        continue
    
    last_nz = nz[0]
    data_bytes = last_nz - 7  # 8 bytes header
    
    # 적합한 n_floats 계산
    n_floats = data_bytes // 4
    if n_floats < 100:
        continue
    
    arr = np.frombuffer(blk[8:8+n_floats*4], dtype=np.float32)
    arr_finite = arr[np.isfinite(arr)]
    
    neg = np.sum(arr_finite < 0)
    large = np.sum(arr_finite > 3)
    in_range = np.sum((arr_finite >= 0) & (arr_finite <= 2))
    
    print(f"  블록{blk_idx:2d}: n={n_floats:4d} min={arr_finite.min() if len(arr_finite)>0 else 'N/A':.4f} "
          f"max={arr_finite.max() if len(arr_finite)>0 else 'N/A':.4f} "
          f"neg={neg} large={large} in_range={in_range}")

# D13 플롯
fig, axes = plt.subplots(2, 3, figsize=(15, 10))
for i, blk_idx in enumerate([1, 2, 3, 5, 10, 20]):
    blk = d13[blk_idx*4096:(blk_idx+1)*4096]
    nz = [j for j in range(4095, 7, -1) if blk[j] != 0]
    if not nz:
        continue
    n_floats = (nz[0] - 7) // 4
    arr = np.frombuffer(blk[8:8+n_floats*4], dtype=np.float32)
    arr = arr[np.isfinite(arr)]
    
    r, c = i // 3, i % 3
    axes[r,c].plot(arr)
    axes[r,c].set_title(f'D13 blk{blk_idx}: {len(arr)} pts, min={arr.min():.3f}')

plt.tight_layout()
plt.savefig('d11_d13_spectra.png', dpi=100)
print("\n그래프: d11_d13_spectra.png")
