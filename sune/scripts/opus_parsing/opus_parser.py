# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: Raima Database 형식의 Bruker OPUS 라이브러리 파일을 읽어 기본 정보와
#            스펙트럼 데이터를 추출한다. (콘솔 출력)
# 실행 방법: python scripts/opus_parsing/opus_parser.py
#            (인자 없음 — library_dir 경로는 하단 __main__ 블록에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
OPUS 파일 파서 - Bruker OPUS 라이브러리 파일 분석
Raima Database 형식의 OPUS 파일에서 스펙트럼 데이터 추출
"""

import struct
import os
from pathlib import Path

def read_opus_file(filepath):
    """OPUS 파일 읽기 및 기본 정보 추출"""
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    # 파일 크기 정보
    file_size = len(data)
    print(f"파일: {os.path.basename(filepath)}")
    print(f"파일 크기: {file_size} bytes")
    
    # 헤더 분석 - Raima Database signature 찾기
    if b'Raima Database Manager' in data:
        idx = data.find(b'Raima Database Manager')
        print(f"Raima Database Manager 위치: {hex(idx)}")
        version_str = data[idx:idx+50].decode('latin-1', errors='ignore')
        print(f"버전 정보: {version_str.strip()}")
    
    # 텍스트 데이터 추출 (화합물 이름, 메타데이터 등)
    print("\n=== 파일에서 찾은 텍스트 정보 ===")
    text_data = []
    current_text = b''
    
    for byte in data:
        if 32 <= byte <= 126:  # 출력 가능한 ASCII
            current_text += bytes([byte])
        else:
            if len(current_text) > 4:  # 4글자 이상
                text_data.append(current_text.decode('latin-1', errors='ignore'))
            current_text = b''
    
    # 중복 제거 및 출력
    unique_texts = sorted(set(text_data))
    for text in unique_texts[:50]:  # 처음 50개만 출력
        if text.strip():
            print(f"  - {text}")
    
    # 스펙트럼 데이터 위치 찾기
    print("\n=== 수치 데이터 분석 ===")
    # 일반적으로 OPUS 파일의 스펙트럼 데이터는 float32 형식
    float_values = []
    for i in range(0, len(data)-3, 4):
        try:
            value = struct.unpack('<f', data[i:i+4])[0]
            if -1e6 < value < 1e6 and value != 0:  # 합리적인 범위의 값만
                float_values.append((i, value))
        except:
            pass
    
    if float_values:
        print(f"찾은 Float32 값: {len(float_values)}개")
        print("샘플 값 (위치: 값):")
        for pos, val in float_values[:20]:
            print(f"  {hex(pos)}: {val:.6f}")
    
    # 스펙트럼 데이터 구간 식별
    print("\n=== 가능한 스펙트럼 데이터 구간 ===")
    
    # OPUS 파일에서 일반적으로 스펙트럼은 뒷부분에 위치
    # 연속된 float 데이터를 찾기
    consecutive_floats = []
    i = 0
    while i < len(data) - 3:
        try:
            value = struct.unpack('<f', data[i:i+4])[0]
            if -1e6 < value < 1e6:
                consecutive_floats.append((i, value))
                i += 4
            else:
                if len(consecutive_floats) > 10:
                    start_pos = consecutive_floats[0][0]
                    end_pos = consecutive_floats[-1][0]
                    count = len(consecutive_floats)
                    print(f"데이터 블록: {hex(start_pos)}-{hex(end_pos)} ({count} 값)")
                consecutive_floats = []
                i += 1
        except:
            i += 1
    
    return {
        'file_size': file_size,
        'has_raima_db': b'Raima Database Manager' in data,
        'float_count': len(float_values)
    }


def analyze_all_files(directory):
    """디렉토리의 모든 OPUS 파일 분석"""
    
    file_path = Path(directory)
    opus_files = sorted(file_path.glob('*.D01')) + sorted(file_path.glob('*.K01'))
    
    print(f"찾은 파일: {len(opus_files)}개\n")
    
    for i, file in enumerate(opus_files[:5], 1):  # 처음 5개 파일만 분석
        print(f"\n{'='*60}")
        print(f"파일 {i}/{len(opus_files)}")
        print(f"{'='*60}")
        read_opus_file(str(file))


if __name__ == "__main__":
    library_dir = "/Users/byeonghoonyoon/PROJECT/RIST/data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library"
    analyze_all_files(library_dir)
