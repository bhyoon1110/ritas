# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS 라이브러리 S01 파일의 헤더·레코드 구조를 분석하고
#            null-종단 문자열을 추출해 화합물 이름 후보를 찾는다. (텍스트 출력)
# 실행 방법: python scripts/opus_parsing/analyze_s01_detailed.py
#            (인자 없음 — S01 경로는 하단 __main__ 블록에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
S01 파일 분석 - 화합물 이름 추출 시도
"""

import struct
import io

def analyze_s01_detailed(filepath):
    """S01 파일의 상세 분석 - 레코드 구조 파악"""
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"파일: {filepath}")
    print(f"크기: {len(data)} bytes\n")
    
    # 헤더 분석
    print("=== 헤더 분석 ===")
    print("처음 512바이트 (16진수 + ASCII):\n")
    
    for offset in range(0, min(512, len(data)), 32):
        hex_str = ' '.join(f'{b:02x}' for b in data[offset:offset+32])
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[offset:offset+32])
        print(f"{offset:04x}  {hex_str:96s}")
        print(f"      {ascii_str}\n")
    
    # 가능한 텍스트 스트링 찾기 (null-terminated)
    print("\n=== Null-terminated 문자열 ===")
    strings = []
    i = 0
    while i < len(data):
        if data[i:i+1] == b'\x00':
            i += 1
        else:
            # 문자열 시작
            end = data.find(b'\x00', i)
            if end == -1:
                end = len(data)
            
            try:
                s = data[i:end].decode('ascii', errors='ignore')
                if len(s) > 5 and any(c.isalpha() for c in s):
                    strings.append((i, s))
            except:
                pass
            
            i = end + 1
    
    print(f"발견된 문자열: {len(strings)}개\n")
    for pos, s in strings[:30]:
        print(f"[0x{pos:06x}] {s}")
    
    # 레코드 크기 추측
    print("\n\n=== 레코드 구조 분석 ===")
    
    # 알려진 크기: 32K (D01) = 8개 스펙트럼 (1005*4 + 18*4 = 4092 bytes each)
    # S01은 메타데이터이므로 다른 구조
    
    # 가능한 레코드 구분자 패턴 찾기
    print(f"전체 파일 크기: {len(data)}")
    
    # 특정 바이트 패턴 찾기
    patterns = [
        (b'\x00\x00\x00\x00', '4개 0'),
        (b'\xff\xff\xff\xff', '4개 FF'),
    ]
    
    for pattern, desc in patterns:
        count = data.count(pattern)
        print(f"{desc} 패턴: {count}개")
    
    # 32-bit 정수값 분석 - 문자 테이블 같은 것 찾기
    print("\n\n=== 32-bit 정수값 분포 ===")
    int_values = defaultdict(int)
    
    for i in range(0, len(data)-3, 4):
        val = struct.unpack('<I', data[i:i+4])[0]
        if val < 1000:
            int_values[val] += 1
    
    # 가장 자주 나오는 값들
    sorted_vals = sorted(int_values.items(), key=lambda x: x[1], reverse=True)
    print("가장 자주 나오는 값:")
    for val, count in sorted_vals[:20]:
        if val < 256:
            print(f"  {val:3d} (0x{val:02x}): {count:4d}회")
        else:
            print(f"  {val:5d} (0x{val:04x}): {count:4d}회")


from collections import defaultdict

if __name__ == "__main__":
    s01_file = "/Users/byeonghoonyoon/PROJECT/RIST/data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/ATR-FTIR O-RING LIBRARY.S01"
    
    analyze_s01_detailed(s01_file)
