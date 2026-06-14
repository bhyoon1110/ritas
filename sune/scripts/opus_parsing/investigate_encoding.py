#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: D12 데이터의 인코딩 방식을 바이트 엔트로피·zlib 압축 여부 등
#            다방면으로 조사한다. (콘솔 출력)
# 실행 방법: python scripts/opus_parsing/investigate_encoding.py
#            (인자 없음 — D12 경로는 상단 LIB_DIR 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""D12 데이터 인코딩 방식을 다방면으로 조사"""
import struct
import numpy as np
import zlib

LIB_DIR = "data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/"
D12 = LIB_DIR + "ATR-FTIR O-RING LIBRARY.D12"

def calc_entropy(data):
    """바이트 엔트로피 계산"""
    freq = np.bincount(np.frombuffer(data, dtype=np.uint8), minlength=256)
    prob = freq[freq>0] / len(data)
    return -np.sum(prob * np.log2(prob))

print("=" * 60)
print("1. 압축/암호화 테스트")
print("=" * 60)

with open(D12, 'rb') as f:
    f.seek(4096)  # block 1
    blk1 = f.read(4096)

print(f"블록 1 엔트로피: {calc_entropy(blk1):.3f} bits/byte")
print(f"(무작위 = 8.0, 텍스트 = 4-5, 압축데이터 = 7.5-8.0)")

# zlib 테스트
for start in [8, 12, 16, 20, 24, 32]:
    try:
        decompressed = zlib.decompress(blk1[start:])
        print(f"[!] zlib 압축 확인! offset {start}에서 시작, 압축 해제 크기: {len(decompressed)}")
    except zlib.error:
        pass

print(f"zlib 압축 없음 (모든 오프셋 실패)")

print()
print("=" * 60)
print("2. 여러 블록 비교 (bytes 16-47)")
print("=" * 60)
with open(D12, 'rb') as f:
    for i in range(0, min(10, 281)):
        f.seek(i * 4096 + 0)
        header = f.read(16)
        h_hex = ' '.join(f'{b:02x}' for b in header)
        print(f"Block {i:3d} header: {h_hex}")

print()
print("=" * 60)
print("3. D12 블록들의 bytes 0-7 패턴 (page marker)")
print("=" * 60)
with open(D12, 'rb') as f:
    markers = []
    for i in range(0, 281):
        f.seek(i * 4096)
        marker = f.read(8)
        markers.append(marker)
    
unique_markers = set(markers)
print(f"고유 페이지 마커 수: {len(unique_markers)}")
for m in list(unique_markers)[:5]:
    cnt = markers.count(m)
    h = ' '.join(f'{b:02x}' for b in m)
    print(f"  {h} → {cnt}개 블록")

print()
print("=" * 60)
print("4. 블록 16에서 시작하는 데이터의 uint16 분포")
print("=" * 60)
with open(D12, 'rb') as f:
    f.seek(4096 + 16)
    data_u16 = np.frombuffer(f.read(2010), dtype=np.uint16)

# 연속값 차이 분석
diff = np.abs(np.diff(data_u16.astype(np.int32)))
print(f"인접 uint16 차이: mean={diff.mean():.0f}, max={diff.max()}, median={np.median(diff):.0f}")
print(f"차이 분포: <100: {np.sum(diff<100)}, <1000: {np.sum(diff<1000)}, >=1000: {np.sum(diff>=1000)}")

print()
print("=" * 60)
print("5. S01 파일 시작 부분 읽기")
print("=" * 60)
try:
    with open(LIB_DIR + "ATR-FTIR O-RING LIBRARY.S01", 'rb') as f:
        s01_start = f.read(200)
    s01_text = s01_start.decode('latin-1')
    print(repr(s01_text[:200]))
except Exception as e:
    print(f"에러: {e}")

print()
print("=" * 60)
print("6. 전체 D12 파일에서 smooth float32 구간 탐색")
print("   (100개 이상 연속, 모두 [0, 2.5] 범위, 낮은 분산)")
print("=" * 60)

with open(D12, 'rb') as f:
    all_data = np.frombuffer(f.read(), dtype=np.float32)

# 4개 바이트씩 이동하며 1005개 float 체크
NPTS = 1005
found_regions = []
for start_float in range(0, len(all_data) - NPTS, 100):  # 400바이트씩 건너뜀
    chunk = all_data[start_float:start_float+NPTS]
    finite_mask = np.isfinite(chunk)
    if not np.all(finite_mask):
        continue
    if np.any(chunk < -0.1) or np.any(chunk > 3.0):
        continue
    # 추가로 평활도 체크
    variance = np.var(chunk)
    if variance > 0.3:  # 분산이 너무 크면 제외
        continue
    found_regions.append((start_float * 4, float(chunk.min()), float(chunk.max()), float(variance)))

if found_regions:
    print(f"[!] smooth float32 구간 {len(found_regions)}개 발견!")
    for r in found_regions[:10]:
        print(f"  offset={r[0]:8d} (0x{r[0]:06X}) min={r[1]:.4f} max={r[2]:.4f} var={r[3]:.4f}")
else:
    print("smooth float32 구간 없음")

print()
print("=" * 60)
print("7. D12를 float64로 읽어서 스펙트럼 탐색")
print("=" * 60)
with open(D12, 'rb') as f:
    all_f64 = np.frombuffer(f.read(), dtype=np.float64)

NPTS64 = 500  # float64로는 약 500 포인트
for start_f64 in range(0, min(len(all_f64)-NPTS64, 10000), 50):
    chunk = all_f64[start_f64:start_f64+NPTS64]
    if not np.all(np.isfinite(chunk)):
        continue
    if np.any(chunk < -0.1) or np.any(chunk > 3.0):
        continue
    variance = np.var(chunk)
    if variance < 0.3:
        print(f"  float64 offset={start_f64*8:8d} (0x{start_f64*8:06X}) "
              f"min={chunk.min():.4f} max={chunk.max():.4f} var={variance:.4f}")

print("float64 탐색 완료")

print()
print("=" * 60)
print("8. ARTs 라이브러리의 ART.D11 구조 확인")
print("=" * 60)
try:
    import os
    art_d11 = "data/Library/ARTs/ART.D11"
    if os.path.exists(art_d11):
        with open(art_d11, 'rb') as f:
            data = f.read()
        print(f"ART.D11 크기: {len(data)} bytes")
        print(f"엔트로피: {calc_entropy(data):.3f} bits/byte")
        print("처음 64바이트:")
        for i in range(0, min(64, len(data)), 16):
            hex_p = ' '.join(f'{b:02x}' for b in data[i:i+16])
            print(f"  0x{i:04X}: {hex_p}")
    else:
        print("ART.D11 없음")
except Exception as e:
    print(f"에러: {e}")
