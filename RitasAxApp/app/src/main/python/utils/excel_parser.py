import csv
import os

import pandas as pd


def read_table(path):
    lower = path.lower()
    if lower.endswith('.csv'):
        return pd.read_csv(path)
    if lower.endswith('.xlsx') or lower.endswith('.xls'):
        return pd.read_excel(path)
    raise ValueError('Unsupported tabular file: {}'.format(path))


def iter_dpt(path):
    points = []
    with open(path, 'r') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) != 2:
                continue
            try:
                points.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return points
