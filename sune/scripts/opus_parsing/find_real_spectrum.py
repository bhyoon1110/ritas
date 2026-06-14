#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS 파일에서 오프셋별 유효성 점수(0~1 양수·부드러운 곡선·음수
#            없음)를 계산해 실제 FTIR 강도 스펙트럼이 저장된 위치를 찾는다. (콘솔 출력)
# 실행 방법: python scripts/opus_parsing/find_real_spectrum.py
#            (인자 없음 — D01 경로는 main() 내부에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
Bruker OPUS 파일 구조 분석 - 실제 스펙트럼 데이터 위치 찾기
"""

import struct
import numpy as np
from pathlib import Path

def analyze_spectrum_pattern(filepath):
    """
    유효한 FTIR 강도 데이터 패턴 찾기
    - 대부분 0-1 범위의 작은 양수
    - 점진적인 변화 (smooth curve)
    - 음수 없음
    """
    with open(filepath, 'rb') as f:
        data = f.read()
    
    file_size = len(data)
    print(f"파일 크기: {file_size} bytes ({file_size/1024:.1f} KB)")
    print(f"\n파일 크기 분석:")
    print(f"- 1005개 float32 = {1005*4} bytes (정규화 스펙트럼)")
    print(f"- 8개 스펙트럼 × 4164 bytes = {8*4164} bytes (예상 D01 크기)")
    print(f"\n실제 D01 크기: {file_size}")
    print(f"스펙트럼 개수 (4164 bytes 기준): {file_size / 4164:.1f}")
    
    # 정규화 패턴 찾기: 작은 양수들이 많은 구간
    print("\n검색 중... (각 오프셋에서 유효성 점수 계산)")
    print("─" * 80)
    
    candidates = []
    
    # 매 100바이트씩 검색
    for offset in range(0, min(file_size - 4020, 1000), 4):
        valid_count = 0
        negative_count = 0
        large_jumps = 0  # 연속 값 사이의 큰 차이
        
        values = []
        for i in range(1005):
            pos = offset + i * 4
            if pos + 4 > file_size:
                break
            
            # Little-Endian 테스트
            try:
                value = struct.unpack('<f', data[pos:pos+4])[0]
                values.append(value)
                
                if 0 <= value <= 1:
                    valid_count += 1
                if value < 0:
                    negative_count += 1
            except:
                break
        
        if len(values) < 1000:
            continue
        
        # 큰 점프 감지 (스펙트럼은 smooth curve여야 함)
        for i in range(1, len(values)):
            if abs(values[i] - values[i-1]) > 0.5:
                large_jumps += 1
        
        # 점수 계산
        score = 0
        if valid_count >= 900:
            score += valid_count - 900
        if negative_count == 0:
            score += 100
        if large_jumps < 50:
            score += 100 - large_jumps
        
        if score > 50:  # 좋은 후보만
            candidates.append({
                'offset': offset,
                'valid': valid_count,
                'negative': negative_count,
                'jumps': large_jumps,
                'min': min(values) if values else 0,
                'max': max(values) if values else 0,
                'mean': np.mean(values) if values else 0,
                'score': score
            })
    
    # 점수 높은 순서로 정렬
    candidates.sort(key=lambda x: x['score'], reverse=True)
    
    print("\n상위 10개 후보:")
    print("─" * 80)
    for i, cand in enumerate(candidates[:10], 1):
        print(f"{i}. 오프셋 0x{cand['offset']:04X} ({cand['offset']})")
        print(f"   유효 (0-1): {cand['valid']}/1005, 음수: {cand['negative']}, " 
              f"큰변화: {cand['jumps']}")
        print(f"   범위: {cand['min']:.8f} ~ {cand['max']:.8f}")
        print(f"   평균: {cand['mean']:.8f}, 점수: {cand['score']}")
        print()
    
    # 최고 점수 후보 상세 분석
    if candidates:
        best = candidates[0]
        print("=" * 80)
        print(f"최고 후보: 오프셋 0x{best['offset']:04X}")
        print("=" * 80)
        
        # 이 오프셋에서 실제 값 추출
        values = []
        for i in range(1005):
            pos = best['offset'] + i * 4
            if pos + 4 > file_size:
                break
            try:
                value = struct.unpack('<f', data[pos:pos+4])[0]
                values.append(value)
            except:
                break
        
        print(f"처음 20개 값:")
        for i, v in enumerate(values[:20], 1):
            print(f"  {i:2d}. {v:.8f}")
        
        print(f"\n마지막 20개 값:")
        for i, v in enumerate(values[-20:], len(values)-19):
            print(f"  {i:2d}. {v:.8f}")

def main():
    filepath = Path("/Users/byeonghoonyoon/PROJECT/RIST/data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/ATR-FTIR O-RING LIBRARY.D01")
    
    print("=" * 80)
    print("OPUS 파일 구조 분석 - 올바른 스펙트럼 데이터 위치 찾기")
    print("=" * 80)
    print(f"파일: {filepath.name}\n")
    
    analyze_spectrum_pattern(filepath)

if __name__ == "__main__":
    main()
