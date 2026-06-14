#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS D01 스펙트럼을 Little/Big-Endian float32로 각각 읽어
#            0~1 정규화 범위 비율 등으로 올바른 바이트 순서를 검증한다. (콘솔 출력)
# 실행 방법: python scripts/opus_parsing/check_endianness.py
#            (인자 없음 — D01 경로는 main() 내부에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
Bruker OPUS 바이너리 포맷 - 바이트 순서 및 정규화 검증
"""

import struct
import numpy as np
from pathlib import Path

def test_spectrum_data(filepath, offset=0x32, num_values=1005, endian='<'):
    """
    float32 데이터 추출 및 분석
    endian: '<' (Little-Endian) or '>' (Big-Endian)
    """
    endian_name = "Little-Endian" if endian == '<' else "Big-Endian"
    fmt = f'{endian}f'  # '<f' or '>f'
    
    with open(filepath, 'rb') as f:
        f.seek(offset)
        
        values = []
        for i in range(num_values):
            data = f.read(4)
            if len(data) < 4:
                break
            try:
                value = struct.unpack(fmt, data)[0]
                values.append(value)
            except:
                break
        
        if not values:
            return None
        
        # 통계
        values_arr = np.array(values)
        
        # 0-1 범위 확인
        in_range_01 = np.sum((values_arr >= 0) & (values_arr <= 1))
        pct_01 = (in_range_01 / len(values)) * 100 if values else 0
        
        # 음수 개수
        negative_count = np.sum(values_arr < 0)
        
        # 유효성 평가
        validity_score = 0
        if pct_01 > 90:
            validity_score += 50
        if negative_count == 0:
            validity_score += 30
        if np.min(values_arr) >= 0:
            validity_score += 20
        
        return {
            'endian': endian_name,
            'values': values,
            'count': len(values),
            'in_01_range': in_range_01,
            'pct_01': pct_01,
            'negative_count': negative_count,
            'min': np.min(values_arr),
            'max': np.max(values_arr),
            'mean': np.mean(values_arr),
            'std': np.std(values_arr),
            'median': np.median(values_arr),
            'validity_score': validity_score,
            'sample': values[:15]
        }

def main():
    filepath = Path("/Users/byeonghoonyoon/PROJECT/RIST/data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/ATR-FTIR O-RING LIBRARY.D01")
    
    print("=" * 90)
    print("OPUS 강도 데이터 검증 (바이트 순서 비교)")
    print("=" * 90)
    print(f"파일: {filepath.name}\n")
    
    # Little-Endian 테스트
    print("┌─ Little-Endian (표준 PC 바이트 순서)")
    print("└" + "─" * 88)
    result_le = test_spectrum_data(filepath, offset=0x32, endian='<')
    
    if result_le:
        print(f"유효한 값 (0≤x≤1): {result_le['in_01_range']:,}/{result_le['count']} ({result_le['pct_01']:.1f}%)")
        print(f"음수 개수: {result_le['negative_count']}")
        print(f"범위: {result_le['min']:.8f} ~ {result_le['max']:.8f}")
        print(f"평균: {result_le['mean']:.8f}")
        print(f"중앙값: {result_le['median']:.8f}")
        print(f"표준편차: {result_le['std']:.8f}")
        print(f"샘플 (처음 15개):")
        for i, v in enumerate(result_le['sample'], 1):
            print(f"  {i:2d}. {v:.8f}")
        print(f"\n평가점수: {result_le['validity_score']}/100")
    
    print("\n" + "─" * 90 + "\n")
    
    # Big-Endian 테스트
    print("┌─ Big-Endian (Motorola 바이트 순서)")
    print("└" + "─" * 88)
    result_be = test_spectrum_data(filepath, offset=0x32, endian='>')
    
    if result_be:
        print(f"유효한 값 (0≤x≤1): {result_be['in_01_range']:,}/{result_be['count']} ({result_be['pct_01']:.1f}%)")
        print(f"음수 개수: {result_be['negative_count']}")
        print(f"범위: {result_be['min']:.8f} ~ {result_be['max']:.8f}")
        print(f"평균: {result_be['mean']:.8f}")
        print(f"중앙값: {result_be['median']:.8f}")
        print(f"표준편차: {result_be['std']:.8f}")
        print(f"샘플 (처음 15개):")
        for i, v in enumerate(result_be['sample'], 1):
            print(f"  {i:2d}. {v:.8f}")
        print(f"\n평가점수: {result_be['validity_score']}/100")
    
    print("\n" + "=" * 90)
    print("추천:")
    print("=" * 90)
    
    if result_le and result_be:
        if result_le['validity_score'] > result_be['validity_score']:
            print(f"✅ Little-Endian 사용 (점수: {result_le['validity_score']}/100)")
        else:
            print(f"✅ Big-Endian 사용 (점수: {result_be['validity_score']}/100)")
    
    # 정규화 분석
    print("\n" + "=" * 90)
    print("정규화 분석:")
    print("=" * 90)
    
    better = result_le if result_le['validity_score'] > result_be['validity_score'] else result_be
    values_arr = np.array(better['values'])
    
    print(f"전체 범위: {better['min']:.8f} ~ {better['max']:.8f}")
    
    if better['min'] >= 0 and better['max'] <= 1:
        print("✅ 이미 0-1 범위로 정규화됨 (투과율 또는 정규화 흡수도)")
    elif better['max'] > 0:
        print(f"⚠️  정규화 필요 (최대값으로 나누기)")
        print(f"   normalized = value / {better['max']:.8f}")
    else:
        print("❌ 데이터가 모두 0 또는 비정상")

if __name__ == "__main__":
    main()
