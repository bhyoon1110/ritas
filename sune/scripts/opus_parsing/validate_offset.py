#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS 바이너리에서 올바른 float32 강도 데이터 오프셋을 여러 후보로
#            테스트해 검증한다. (콘솔 출력)
# 실행 방법: python scripts/opus_parsing/validate_offset.py
#            (인자 없음 — D01 경로는 main() 내부에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
Bruker OPUS 바이너리 포맷 오프셋 검증
올바른 float32 강도 데이터 찾기
"""

import struct
import numpy as np
from pathlib import Path

def test_offset(filepath, offset, num_spectra=5):
    """
    특정 오프셋에서 데이터 추출 및 검증
    valid: 0 <= value <= 1 범위의 값들이 많은지 확인
    """
    with open(filepath, 'rb') as f:
        f.seek(offset)
        
        valid_count = 0
        negative_count = 0
        huge_count = 0  # 1e6 이상인 거대한 값
        values = []
        
        # 5개 스펙트럼 × 1005개 포인트 = 5025개 float32
        for i in range(1005 * num_spectra):
            try:
                data = f.read(4)
                if len(data) < 4:
                    break
                value = struct.unpack('<f', data)[0]
                values.append(value)
                
                # 유효성 판단
                if 0 <= value <= 1:
                    valid_count += 1
                elif value < 0:
                    negative_count += 1
                elif abs(value) > 1e6:
                    huge_count += 1
            except:
                break
        
        if len(values) == 0:
            return None
        
        valid_pct = (valid_count / len(values)) * 100 if values else 0
        
        return {
            'offset': offset,
            'values_read': len(values),
            'valid_count': valid_count,
            'valid_percent': valid_pct,
            'negative_count': negative_count,
            'huge_count': huge_count,
            'min': min(values),
            'max': max(values),
            'mean': np.mean(values),
            'std': np.std(values),
            'sample': values[:10]
        }

def main():
    filepath = Path("/Users/byeonghoonyoon/PROJECT/RIST/data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/ATR-FTIR O-RING LIBRARY.D01")
    
    print("=" * 80)
    print("OPUS 바이너리 포맷 오프셋 검증")
    print("=" * 80)
    print(f"파일: {filepath.name}")
    print(f"파일 크기: {filepath.stat().st_size} bytes\n")
    
    # 다양한 오프셋 테스트
    offsets_to_test = [
        0x32,      # 현재 사용 중
        0x40,      # 다음 32바이트
        0x50,      # 더 뒤
        0x64,      # 100바이트
        0x80,      # 128바이트
        0x100,     # 256바이트
        0x200,     # 512바이트
        0x400,     # 1024바이트
    ]
    
    results = []
    for offset in offsets_to_test:
        result = test_offset(filepath, offset)
        if result:
            results.append(result)
            
            print(f"\n{'─'*80}")
            print(f"오프셋: 0x{offset:02X} ({offset})")
            print(f"{'─'*80}")
            print(f"읽은 값: {result['values_read']}개")
            print(f"유효한 값 (0≤x≤1): {result['valid_count']:,}개 ({result['valid_percent']:.1f}%)")
            print(f"음수: {result['negative_count']:,}개")
            print(f"거대한 값 (|x|>1e6): {result['huge_count']:,}개")
            print(f"범위: {result['min']:.6e} ~ {result['max']:.6e}")
            print(f"평균: {result['mean']:.6e}")
            print(f"표준편차: {result['std']:.6e}")
            print(f"샘플: {[f'{v:.6f}' for v in result['sample'][:5]]}")
    
    print(f"\n\n{'='*80}")
    print("추천 오프셋 (유효한 값 비율 높은 순서):")
    print(f"{'='*80}")
    
    sorted_results = sorted(results, key=lambda x: x['valid_percent'], reverse=True)
    for i, result in enumerate(sorted_results[:5], 1):
        print(f"{i}. 오프셋 0x{result['offset']:02X}: {result['valid_percent']:.1f}% 유효")

if __name__ == "__main__":
    main()
