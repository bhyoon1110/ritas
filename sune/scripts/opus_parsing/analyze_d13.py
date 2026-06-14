#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: Bruker OPUS 라이브러리의 D13(·D10) 파일을 헥스덤·오프셋별
#            float32 품질로 상세 분석하여 실제 스펙트럼 데이터 위치를 탐색한다.
#            결과 그래프를 d13_analysis.png 로 저장(디버그용 matplotlib).
# 실행 방법: python scripts/opus_parsing/analyze_d13.py
#            (인자 없음 — D13/D10 경로는 상단 LIB_DIR 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""D13 파일 상세 분석 - 실제 스펙트럼 데이터 탐색"""
import struct
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

LIB_DIR = "data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/"
D13 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D13"
D10 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D10"

with open(D13, 'rb') as f:
    d13 = f.read()

print(f"D13 크기: {len(d13)} bytes = {len(d13)//4096} 블록")

# D13 블록 1 헥스덤프
print("\nD13 블록 1 (처음 128 bytes):")
blk1 = d13[4096:8192]
for i in range(0, 128, 16):
    h = ' '.join(f'{b:02x}' for b in blk1[i:i+16])
    a = ''.join(chr(b) if 32<=b<127 else '.' for b in blk1[i:i+16])
    print(f"  0x{4096+i:05X}: {h}  {a}")

print("\nD13 블록 1 float32 (offset 8부터 처음 40개):")
arr = np.frombuffer(blk1[8:8+4020], dtype=np.float32)
finite = arr[np.isfinite(arr)]
print(f"  finite: {np.sum(np.isfinite(arr))}, neg: {np.sum(arr<0)}, large: {np.sum(arr>2)}")
print(f"  min={arr.min():.6f}, max={arr.max():.6f}")
print(f"  처음 20개: {arr[:20].tolist()}")

# 모든 오프셋 시도 (8바이트 스텝)
print("\n다양한 오프셋에서 float32 품질 검사:")
for off in range(8, 200, 4):
    arr_test = np.frombuffer(blk1[off:off+4020], dtype=np.float32)
    if not np.all(np.isfinite(arr_test)):
        continue
    neg = np.sum(arr_test < -0.01)
    large = np.sum(arr_test > 3.0)
    in_range = np.sum((arr_test >= 0) & (arr_test <= 2))
    if neg == 0 and large == 0 and in_range > 800:
        print(f"  [GOOD] off={off}: min={arr_test.min():.4f} max={arr_test.max():.4f} in_range={in_range}")

# 0을 제외한 값들의 분포
non_zero = finite[(finite > 1e-6) | (finite < -1e-6)]
print(f"\n비제로 값: {len(non_zero)}")
if len(non_zero) > 0:
    print(f"  min={non_zero.min():.4f} max={non_zero.max():.4f} mean={non_zero.mean():.4f}")

# 블록 1-27 스캔
print("\n" + "=" * 60)
print("D13 모든 블록 스캔 (블록 1~26)")
print("=" * 60)

for blk_idx in range(1, 27):
    blk = d13[blk_idx*4096:(blk_idx+1)*4096]
    # 최선 오프셋에서 float32
    for off in [8, 12, 16, 20, 24, 28, 32, 36, 40, 48, 52, 56, 64, 76, 100, 108]:
        arr = np.frombuffer(blk[off:off+4020], dtype=np.float32)
        if not np.all(np.isfinite(arr)):
            continue
        neg = np.sum(arr < -0.01)
        large = np.sum(arr > 3.0)
        in_range = np.sum((arr >= 0) & (arr <= 2))
        if neg == 0 and large == 0 and in_range > 900:
            print(f"  [EXCELLENT] 블록{blk_idx} off={off}: "
                  f"min={arr.min():.4f} max={arr.max():.4f} in_range={in_range}")
            break
        elif neg == 0 and in_range > 700:
            print(f"  [GOOD] 블록{blk_idx} off={off}: "
                  f"min={arr.min():.4f} max={arr.max():.4f} in_range={in_range}")

# D13 블록 1을 offset 8에서 플롯
fig, axes = plt.subplots(3, 3, figsize=(15, 12))
for i, (blk_idx, off) in enumerate([(1,8),(1,16),(1,52),(2,8),(3,8),(5,8),(10,8),(15,8),(20,8)]):
    blk = d13[blk_idx*4096:(blk_idx+1)*4096]
    arr = np.frombuffer(blk[off:off+4020], dtype=np.float32)
    r = i // 3
    c = i % 3
    arr_f = arr[np.isfinite(arr)]
    axes[r,c].plot(arr_f[:500])
    axes[r,c].set_title(f'D13 블k{blk_idx} off={off} min={arr_f.min():.3f}')
plt.tight_layout()
plt.savefig('d13_analysis.png', dpi=100)
print("\n그래프 저장: d13_analysis.png")

# D10 블록 1도 확인
print("\n" + "=" * 60)
print("D10 블록 1 (암호화된 데이터) vs D13 블록 1")
print("=" * 60)
with open(D10, 'rb') as f:
    d10_blk1 = f.read(8192)[4096:]

print("D10 블록1 처음 32 bytes:")
h = ' '.join(f'{b:02x}' for b in d10_blk1[:32])
print(f"  {h}")

print("D13 블록1 처음 32 bytes:")
h = ' '.join(f'{b:02x}' for b in blk1[:32])
print(f"  {h}")

# XOR 차이?
xor = bytes(a^b for a,b in zip(d10_blk1[:32], blk1[:32]))
print("XOR:")
h = ' '.join(f'{b:02x}' for b in xor)
print(f"  {h}")
