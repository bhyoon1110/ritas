# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: OPUS D01 파일의 각 바이트를 10진수로 변환해 그래프(opus_graph.png)와
#            CSV(opus_decimal.csv)로 저장한다. (가장 단순한 바이트 시각화 디버그용)
# 실행 방법: python scripts/opus_parsing/opus_to_graph.py
#            (인자 없음 — file_path·출력 경로는 상단 상수에 내장)
# ─────────────────────────────────────────────────────────────────────────────
import os
import matplotlib.pyplot as plt
import numpy as np

# OPUS 라이브러리 파일 경로
file_path = "/Users/byeonghoonyoon/PROJECT/RIST/data/Library/ATR-FTIR LIBRARY O-RING - 1816158 - SL/Library/ATR-FTIR O-RING LIBRARY.D01"

# 바이너리 모드로 파일 읽기
with open(file_path, 'rb') as f:
    data = f.read()

# 각 바이트를 10진수로 변환
decimal_values = list(data)

print(f"파일 크기: {len(decimal_values)} bytes")
print(f"처음 20개 값: {decimal_values[:20]}")
print(f"최소값: {min(decimal_values)}, 최대값: {max(decimal_values)}")

# 그래프 그리기
plt.figure(figsize=(14, 6))

# 1. 전체 데이터 그래프
plt.subplot(2, 1, 1)
plt.plot(decimal_values, linewidth=0.5)
plt.title('OPUS Library File - Byte Values (Decimal)')
plt.xlabel('Byte Position')
plt.ylabel('Value')
plt.grid(True, alpha=0.3)

# 2. 히스토그램
plt.subplot(2, 1, 2)
plt.hist(decimal_values, bins=256, edgecolor='black', alpha=0.7)
plt.title('Byte Value Distribution')
plt.xlabel('Value')
plt.ylabel('Frequency')
plt.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('/Users/byeonghoonyoon/PROJECT/RIST/opus_graph.png', dpi=150)
print("그래프 저장됨: /Users/byeonghoonyoon/PROJECT/RIST/opus_graph.png")

# 10진수 데이터를 CSV로도 저장
with open('/Users/byeonghoonyoon/PROJECT/RIST/opus_decimal.csv', 'w') as f:
    f.write('Position,DecimalValue\n')
    for i, val in enumerate(decimal_values):
        f.write(f'{i},{val}\n')

print("10진수 데이터 저장됨: /Users/byeonghoonyoon/PROJECT/RIST/opus_decimal.csv")

plt.show()
