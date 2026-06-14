# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS 라이브러리의 K01·K02·K03 파일에서 화합물 메타데이터를,
#            S01·S02 파일에서 구조를 추출·분석한다. (콘솔 출력)
# 실행 방법: python scripts/opus_parsing/opus_metadata_analysis.py
#            (인자 없음 — library_dir 경로는 main() 내부에 내장)
# ─────────────────────────────────────────────────────────────────────────────
"""
OPUS 라이브러리 메타데이터 추출
K01, K02, K03 파일에서 화합물 정보 추출
S01, S02 파일 분석
"""

import struct
import os
from pathlib import Path
import re

def analyze_file_structure(filepath):
    """파일 구조 분석"""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    filename = Path(filepath).name
    filesize = len(data)
    
    print(f"\n{'='*70}")
    print(f"파일: {filename}")
    print(f"크기: {filesize} bytes")
    print(f"{'='*70}")
    
    # 텍스트 추출
    print("\n=== 포함된 텍스트 정보 ===")
    text_strings = []
    current_text = b''
    
    for byte in data:
        if 32 <= byte <= 126:  # 출력 가능한 ASCII
            current_text += bytes([byte])
        else:
            if len(current_text) > 3:
                try:
                    text = current_text.decode('latin-1', errors='ignore')
                    if text.strip():
                        text_strings.append(text)
                except:
                    pass
            current_text = b''
    
    # 중복 제거 및 정렬
    unique_texts = sorted(set(text_strings))
    
    # 화합물 이름일 가능성 있는 문자열 필터링
    potential_names = []
    for text in unique_texts:
        # 길이 3-50, 특수문자 제한
        if 3 < len(text) < 100 and not text.startswith('\x00'):
            if any(c.isalpha() for c in text):  # 최소 하나의 문자 포함
                potential_names.append(text)
    
    # 상위 문자열 출력
    print(f"발견된 텍스트: {len(unique_texts)}개\n")
    print("주요 텍스트 (20개):")
    for text in potential_names[:50]:
        if len(text) > 2:
            print(f"  - '{text}'")
    
    # 16진수 헤더 분석
    print("\n=== 16진수 헤더 (처음 128바이트) ===")
    for offset in range(0, min(128, len(data)), 16):
        hex_str = ' '.join(f'{b:02x}' for b in data[offset:offset+16])
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[offset:offset+16])
        print(f"{offset:04x}  | {hex_str:48s} | {ascii_str}")
    
    # 숫자 패턴 찾기 (32-bit integers)
    print("\n=== 32-bit 정수값 (처음 부분) ===")
    integers = []
    for i in range(0, min(256, len(data)-3), 4):
        val = struct.unpack('<I', data[i:i+4])[0]
        if val > 0 and val < 1000000:
            integers.append((i, val))
    
    for pos, val in integers[:15]:
        print(f"  Offset 0x{pos:04x}: {val:10d} (0x{val:08x})")
    
    return data, unique_texts


def extract_index_info(filepath):
    """OPUS 인덱스 파일(K01, K02, K03)에서 정보 추출"""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    filename = Path(filepath).name
    
    print(f"\n{'='*70}")
    print(f"인덱스 파일 분석: {filename}")
    print(f"{'='*70}")
    
    # 텍스트 섹션 추출
    text_data = []
    for i in range(len(data)):
        if 32 <= data[i] <= 126:
            text_data.append(chr(data[i]))
        else:
            text_data.append(' ')
    
    text_str = ''.join(text_data)
    
    # 라인 분리
    lines = text_str.split('\x00')
    
    print(f"\n발견된 텍스트 라인: {len([l for l in lines if l.strip()])}개\n")
    
    meaningful_lines = []
    for line in lines:
        line = line.strip()
        if line and len(line) > 3:
            meaningful_lines.append(line)
            if len(meaningful_lines) <= 30:
                print(f"  [{len(meaningful_lines):2d}] {line}")
    
    return meaningful_lines


def extract_sample_info(filepath):
    """S01, S02 파일에서 정보 추출"""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    filename = Path(filepath).name
    
    print(f"\n{'='*70}")
    print(f"샘플 파일: {filename}")
    print(f"파일 크기: {len(data)} bytes")
    print(f"{'='*70}")
    
    # 텍스트 추출
    text_data = []
    for byte in data:
        if 32 <= byte <= 126:
            text_data.append(chr(byte))
        else:
            if text_data and text_data[-1] != '\n':
                text_data.append('\n')
    
    text_str = ''.join(text_data)
    lines = [l.strip() for l in text_str.split('\n') if l.strip()]
    
    print(f"\n포함된 텍스트 ({len(lines)}줄):\n")
    for i, line in enumerate(lines[:50], 1):
        print(f"  [{i:2d}] {line}")
    
    # 16진수 헤더
    print("\n=== 16진수 (처음 256바이트) ===")
    for offset in range(0, min(256, len(data)), 16):
        hex_str = ' '.join(f'{b:02x}' for b in data[offset:offset+16])
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[offset:offset+16])
        print(f"{offset:04x}  | {hex_str:48s} | {ascii_str}")
    
    return text_str


def main():
    library_dir = "/Users/byeonghoonyoon/PROJECT/RIST/data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library"
    
    # 분석할 파일들
    files_to_analyze = [
        f"{library_dir}/ATR-FTIR O-RING LIBRARY.K01",
        f"{library_dir}/ATR-FTIR O-RING LIBRARY.K02",
        f"{library_dir}/ATR-FTIR O-RING LIBRARY.K03",
        f"{library_dir}/ATR-FTIR O-RING LIBRARY.S01",
        f"{library_dir}/ATR-FTIR O-RING LIBRARY.S02",
    ]
    
    print("\n" + "="*70)
    print("OPUS 라이브러리 메타데이터 분석")
    print("="*70)
    
    for filepath in files_to_analyze:
        if os.path.exists(filepath):
            filename = Path(filepath).name
            
            if filename.endswith('.K01') or filename.endswith('.K02') or filename.endswith('.K03'):
                extract_index_info(filepath)
            elif filename.endswith('.S01') or filename.endswith('.S02'):
                extract_sample_info(filepath)
        else:
            print(f"파일 없음: {filepath}")


if __name__ == "__main__":
    main()
