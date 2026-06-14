# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS D01 파일을 특정 오프셋·메타데이터 기반으로 좀 더 정교하게
#            파싱하고 바이트 분석 그래프(opus_byte_analysis.png)를 저장한다. (디버그용)
# 실행 방법: python scripts/opus_parsing/opus_analysis_v2.py
#            (인자 없음 — D01 경로는 하단 __main__ 블록에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
Bruker OPUS 파일 분석 - 더 정교한 접근
특정 오프셋과 메타데이터 기반 파싱
"""

import struct
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

def read_opus_spectrum_v2(filepath):
    """OPUS 파일에서 스펙트럼 데이터 추출 (V2)"""
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"파일: {Path(filepath).name}")
    print(f"크기: {len(data)} bytes")
    
    # Bruker OPUS 파일 구조 분석
    # D01 파일은 데이터 파일, K01은 인덱스
    
    # 헤더 확인
    if b'Raima' in data[:100]:
        print("✓ Raima Database 형식 확인")
    
    # 스펙트럼 범위: 일반적으로 4000-400 cm-1 (ATR-FTIR)
    # 또는 포인트 수로 계산 필요
    
    # 바이너리 데이터에서 정수값 추출 시도 (Little-endian 32-bit)
    print("\n=== 32-bit 정수값 분석 ===")
    integers = []
    for i in range(0, len(data)-3, 4):
        val = struct.unpack('<I', data[i:i+4])[0]
        if val > 0 and val < 100000:  # 합리적인 범위
            integers.append((i, val))
    
    print(f"합리적인 정수값: {len(integers)}개")
    if integers:
        print("샘플 값 (위치: 값):")
        for pos, val in integers[:10]:
            print(f"  {hex(pos)}: {val}")
    
    # 다양한 오프셋에서 데이터 추출 시도
    print("\n=== 다양한 오프셋에서 Float 추출 ===")
    
    test_offsets = [0x32, 0x100, 0x200, 0x400, 0x800, 0x1000]
    
    for offset in test_offsets:
        if offset + 4000 < len(data):  # 약 1000개 float을 읽을 수 있는지 확인
            floats = []
            valid_count = 0
            
            for i in range(offset, offset + 4000, 4):
                try:
                    val = struct.unpack('<f', data[i:i+4])[0]
                    floats.append(val)
                    if 0 <= val <= 1:  # FTIR intensity는 보통 0-1 범위
                        valid_count += 1
                except:
                    pass
            
            valid_pct = (valid_count / len(floats)) * 100 if floats else 0
            print(f"오프셋 {hex(offset)}: {valid_pct:.1f}% 유효 값 (0-1 범위)")
            
            if valid_pct > 50:  # 50% 이상이 0-1 범위면 가능성 높음
                print(f"  → 이 부분이 스펙트럼으로 보임!")
                print(f"  → 범위: {min(floats):.6f} ~ {max(floats):.6f}")
                print(f"  → 평균: {np.mean(floats):.6f}")
    
    # 16진수 덤프로 구조 확인
    print("\n=== 파일 구조 (16진수) ===")
    print("오프셋  | 데이터")
    print("-" * 60)
    for offset in range(0, min(len(data), 0x100), 16):
        hex_str = ' '.join(f'{b:02x}' for b in data[offset:offset+16])
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[offset:offset+16])
        print(f"{offset:06x}  | {hex_str:48s} | {ascii_str}")


def visualize_raw_bytes(filepath):
    """바이트 값을 직접 그래프로 표시"""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    # 처음 몇 바이트를 10진수로 변환
    byte_values = list(data)
    
    # 여러 섹션으로 나누어 표시
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # 섹션 1: 전체 바이트 값
    axes[0, 0].plot(byte_values, linewidth=0.5)
    axes[0, 0].set_title('All Bytes (Decimal)')
    axes[0, 0].set_ylabel('Value (0-255)')
    axes[0, 0].grid(True, alpha=0.3)
    
    # 섹션 2: 히스토그램
    axes[0, 1].hist(byte_values, bins=256, edgecolor='black', alpha=0.7)
    axes[0, 1].set_title('Byte Value Distribution')
    axes[0, 1].set_xlabel('Value')
    axes[0, 1].set_ylabel('Count')
    axes[0, 1].grid(True, alpha=0.3)
    
    # 섹션 3: 평활화된 데이터 (이동 평균)
    window_size = 100
    smoothed = np.convolve(byte_values, np.ones(window_size)/window_size, mode='valid')
    axes[1, 0].plot(smoothed, linewidth=1, color='red')
    axes[1, 0].set_title(f'Smoothed Data ({window_size}-point moving average)')
    axes[1, 0].set_ylabel('Average Value')
    axes[1, 0].grid(True, alpha=0.3)
    
    # 섹션 4: 차분 (변화량)
    diff = np.diff(byte_values)
    axes[1, 1].plot(diff, linewidth=0.5, color='green')
    axes[1, 1].set_title('Byte-to-Byte Differences')
    axes[1, 1].set_ylabel('Difference')
    axes[1, 1].grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('/Users/byeonghoonyoon/PROJECT/RIST/opus_byte_analysis.png', dpi=150)
    print("바이트 분석 그래프 저장: /Users/byeonghoonyoon/PROJECT/RIST/opus_byte_analysis.png")
    plt.close()


if __name__ == "__main__":
    filepath = "/Users/byeonghoonyoon/PROJECT/RIST/data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/ATR-FTIR O-RING LIBRARY.D01"
    
    print("="*70)
    print("Bruker OPUS 파일 상세 분석 (V2)")
    print("="*70 + "\n")
    
    read_opus_spectrum_v2(filepath)
    
    print("\n" + "="*70)
    print("바이트 시각화 생성 중...")
    print("="*70)
    visualize_raw_bytes(filepath)
