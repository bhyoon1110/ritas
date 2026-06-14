#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS D01 파일을 바이트 수준으로 완전 해부한다. 헥덤·비제로
#            float32 구간 탐색·오프셋별 디버그 PNG를 생성한다. (디버그용 matplotlib)
# 실행 방법: python scripts/opus_parsing/deep_analysis.py
#            (인자 없음 — LIBRARY_DIR 경로는 상단 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
OPUS D01 파일 완전 해부 - 실제 바이트 수준 분석
"""

import struct
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

LIBRARY_DIR = Path("/Users/byeonghoonyoon/PROJECT/RIST/data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library")

def hexdump(data, start=0, length=256, label=""):
    if label:
        print(f"\n{'='*70}\n{label}\n{'='*70}")
    for i in range(start, min(start + length, len(data)), 16):
        hex_str = ' '.join(f'{data[i+j]:02x}' for j in range(16) if i+j < len(data))
        asc_str = ''.join(chr(data[i+j]) if 32 <= data[i+j] < 127 else '.' for j in range(16) if i+j < len(data))
        print(f"0x{i:04X}: {hex_str:<47}  {asc_str}")

def find_nonzero_float32_regions(data, label=""):
    """파일 전체에서 비제로 float32 값 찾기"""
    n = len(data) // 4
    all_vals = np.frombuffer(data[:n*4], dtype='<f4')
    
    finite_mask = np.isfinite(all_vals)
    reasonable_mask = finite_mask & (np.abs(all_vals) < 10.0)
    nonzero_mask = reasonable_mask & (np.abs(all_vals) > 1e-6)
    
    positions = np.where(nonzero_mask)[0]
    
    print(f"\n{label}")
    print(f"전체 float32 값: {n:,}")
    print(f"유한(finite) 값: {finite_mask.sum():,}")
    print(f"합리적 범위 (|x|<10): {reasonable_mask.sum():,}")
    print(f"비제로 (|x|>1e-6): {nonzero_mask.sum():,}")
    
    if len(positions) > 0:
        print(f"첫 비제로 float 위치: index {positions[0]} (offset 0x{positions[0]*4:04X})")
        print(f"마지막 비제로 float 위치: index {positions[-1]} (offset 0x{positions[-1]*4:04X})")
        
        # 연속 구간 찾기
        if len(positions) > 1:
            gaps = np.diff(positions)
            big_gaps = np.where(gaps > 10)[0]
            
            regions = []
            start_idx = 0
            for gap_pos in big_gaps:
                end_idx = gap_pos
                if positions[end_idx] - positions[start_idx] > 5:
                    regions.append((positions[start_idx], positions[end_idx]))
                start_idx = gap_pos + 1
            regions.append((positions[start_idx], positions[-1]))
            
            print(f"\n비제로 float32 연속 구간 ({len(regions)}개):")
            for rs, re in regions[:20]:
                byte_start = rs * 4
                byte_end = re * 4
                count = re - rs + 1
                sample = all_vals[rs]
                print(f"  offset 0x{byte_start:05X}-0x{byte_end:05X}: {count} floats, 첫값={sample:.6f}")
    
    return positions, all_vals

def compute_file_sizes():
    """정확한 파일 크기로 블록 크기 계산"""
    known_counts = {
        'D01': 8, 'D02': 5, 'D03': 5, 'D04': 2, 'D05': 23,
        'D06': 1, 'D07': 4, 'D08': 5, 'D09': 1, 'D10': 72,
        'D11': 1, 'D12': 281, 'D13': 27, 'D14': 1
    }
    
    print("="*70)
    print("정확한 파일 크기 및 블록 크기")
    print("="*70)
    
    block_sizes = {}
    for dname, count in known_counts.items():
        fpath = LIBRARY_DIR / f"ATR-FTIR O-RING LIBRARY.{dname}"
        size = fpath.stat().st_size
        bs = size / count
        block_sizes[dname] = bs
        print(f"{dname}: {size:,} bytes / {count} spectra = {bs:.2f} bytes/spectrum")
    
    # 모두 같은 블록 크기인지 확인
    unique_bs = set(round(bs) for bs in block_sizes.values())
    print(f"\n블록 크기 종류: {unique_bs}")
    return block_sizes

def scan_block_offsets(data, block_size, n_spectra, file_label=""):
    """블록 내 최적 데이터 오프셋 찾기"""
    file_size = len(data)
    
    print(f"\n{'='*70}")
    print(f"{file_label} - 블록 내 오프셋 스캔 (block_size={block_size})")
    print(f"{'='*70}")
    
    results = []
    
    for data_offset in range(0, min(block_size - 1005*4, 300), 4):
        stats = []
        for blk in range(n_spectra):
            abs_off = blk * block_size + data_offset
            if abs_off + 4020 > file_size:
                break
            chunk = data[abs_off:abs_off + 4020]
            vals = np.frombuffer(chunk, dtype='<f4')
            
            neg = int(np.sum(vals < -0.01))
            huge = int(np.sum(np.abs(vals) > 5.0))
            nan_inf = int(np.sum(~np.isfinite(vals)))
            nonzero = int(np.sum(np.abs(vals) > 1e-5))
            
            stats.append({'neg': neg, 'huge': huge, 'nan': nan_inf, 'nonzero': nonzero,
                          'max': float(np.max(np.abs(vals[np.isfinite(vals)])) if np.any(np.isfinite(vals)) else 0)})
        
        if not stats:
            continue
        
        total_neg = sum(s['neg'] for s in stats)
        total_huge = sum(s['huge'] for s in stats)
        total_nan = sum(s['nan'] for s in stats)
        total_nonzero = sum(s['nonzero'] for s in stats)
        
        # 최고 점수 = 비제로 많고, 음수/거대값/NaN 없음
        score = total_nonzero * 10 - total_neg * 10000 - total_huge * 1000 - total_nan * 100000
        
        results.append({
            'offset': data_offset,
            'nonzero': total_nonzero,
            'neg': total_neg,
            'huge': total_huge,
            'nan': total_nan,
            'score': score
        })
    
    results.sort(key=lambda x: x['score'], reverse=True)
    
    print(f"{'오프셋':>10} {'비제로':>8} {'음수':>8} {'거대값':>8} {'NaN':>8} {'점수':>10}")
    print("-"*60)
    for r in results[:20]:
        marker = " <<<" if r['score'] == results[0]['score'] else ""
        print(f"0x{r['offset']:04X}({r['offset']:3d}) {r['nonzero']:>8} {r['neg']:>8} {r['huge']:>8} {r['nan']:>8} {r['score']:>10}{marker}")
    
    return results[0]['offset'] if results else 50

def extract_and_verify(data, block_size, data_offset, n_spectra, label=""):
    """올바른 파라미터로 스펙트럼 추출 및 검증"""
    file_size = len(data)
    wavenums = np.linspace(4000, 400, 1005)
    spectra = []
    
    for blk in range(n_spectra):
        abs_off = blk * block_size + data_offset
        if abs_off + 4020 > file_size:
            break
        vals = np.frombuffer(data[abs_off:abs_off + 4020], dtype='<f4').copy()
        spectra.append(vals)
    
    print(f"\n{label} - 추출 결과 (block={block_size}, offset=0x{data_offset:02X}):")
    for i, s in enumerate(spectra):
        neg = np.sum(s < -0.001)
        nonzero = np.sum(s > 0.001)
        print(f"  Spec {i+1:2d}: min={s.min():+.6f} max={s.max():+.6f} nonzero={nonzero} neg={neg}")
    
    return spectra, wavenums

def plot_spectra(spectra, wavenums, title, output_path):
    n = len(spectra)
    cols = min(4, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols*5, rows*3))
    axes = np.array(axes).flatten()
    
    for i, spec in enumerate(spectra):
        axes[i].plot(wavenums, spec, 'b-', lw=0.6)
        axes[i].set_xlim(4000, 400)
        axes[i].set_xlabel('Wavenumber (cm⁻¹)', fontsize=7)
        axes[i].set_ylabel('Intensity', fontsize=7)
        axes[i].set_title(f'Spectrum {i+1}\nmax={spec.max():.4f}', fontsize=7)
        axes[i].tick_params(labelsize=6)
    
    for j in range(n, len(axes)):
        axes[j].set_visible(False)
    
    plt.suptitle(title, fontsize=9)
    plt.tight_layout()
    plt.savefig(output_path, dpi=100)
    plt.close()
    print(f"그래프 저장: {output_path}")

def main():
    # 1. 정확한 파일 크기 계산
    block_sizes = compute_file_sizes()
    
    # 모든 파일의 블록 크기가 동일한지 확인
    bs_set = set(round(v) for v in block_sizes.values())
    universal_block_size = round(list(block_sizes.values())[0])
    print(f"\n✅ 공통 블록 크기: {universal_block_size} bytes")
    
    # 2. D01 파일 바이너리 분석
    d01_path = LIBRARY_DIR / "ATR-FTIR O-RING LIBRARY.D01"
    with open(d01_path, 'rb') as f:
        d01_data = f.read()
    
    hexdump(d01_data, 0, 128, "D01 파일 헤더 (첫 128바이트)")
    hexdump(d01_data, universal_block_size, 128, f"D01 두 번째 블록 시작 (offset={universal_block_size}=0x{universal_block_size:04X})")
    
    # 3. D01 전체 비제로 float32 찾기
    positions, all_floats = find_nonzero_float32_regions(d01_data, "D01 float32 분석")
    
    # 4. 비제로 값들이 어떻게 생겼는지 보기
    if len(positions) > 0:
        print(f"\nD01 비제로 float32 샘플 (처음 30개):")
        for pos in positions[:30]:
            print(f"  offset 0x{pos*4:05X}: {all_floats[pos]:.8f}")
    
    # 5. 블록 내 최적 오프셋 찾기 (D01 기준)
    best_off_d01 = scan_block_offsets(d01_data, universal_block_size, 8, "D01")
    
    # 6. 위에서 찾은 비제로 구간을 기반으로 다른 오프셋도 시도
    if len(positions) > 0:
        # 첫 비제로 float32의 오프셋을 블록 크기로 나눈 나머지
        first_nonzero_offset = int(positions[0]) * 4
        within_block_offset = first_nonzero_offset % universal_block_size
        print(f"\n첫 비제로 데이터의 블록 내 오프셋: {within_block_offset} (0x{within_block_offset:04X})")
        
        # 그 오프셋도 테스트
        candidate_offsets = sorted(set([best_off_d01, within_block_offset, 50, 0]))
        
        print(f"\n{'='*70}")
        print("후보 오프셋들로 D01 스펙트럼 추출 및 시각화")
        print(f"{'='*70}")
        
        for coff in candidate_offsets:
            spectra, wn = extract_and_verify(d01_data, universal_block_size, coff, 8, f"D01 offset=0x{coff:02X}")
            
            # 비제로 스펙트럼만 플롯
            valid = [s for s in spectra if np.sum(s > 0.001) > 20]
            if valid:
                plot_spectra(spectra, wn, 
                            f"D01 | block={universal_block_size}, data_offset=0x{coff:02X}",
                            f"/Users/byeonghoonyoon/PROJECT/RIST/debug_offset_{coff:03d}.png")
    
    # 7. D12도 분석 (가장 큰 파일, 문제 파일)
    print(f"\n{'='*70}")
    print("D12 파일 분석 (281개 스펙트럼)")
    print(f"{'='*70}")
    d12_path = LIBRARY_DIR / "ATR-FTIR O-RING LIBRARY.D12"
    with open(d12_path, 'rb') as f:
        d12_data = f.read()
    
    hexdump(d12_data, 0, 64, "D12 첫 64바이트")
    hexdump(d12_data, universal_block_size, 64, f"D12 두 번째 블록 시작")
    
    positions12, floats12 = find_nonzero_float32_regions(d12_data, "D12 float32 분석")
    
    if len(positions12) > 0:
        print(f"\nD12 비제로 float32 샘플 (처음 20개):")
        for pos in positions12[:20]:
            print(f"  offset 0x{pos*4:05X}: {floats12[pos]:.8f}")

if __name__ == "__main__":
    main()
