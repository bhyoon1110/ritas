# LIM

XRD raw 데이터와 ICDD Card PDF를 기반으로 분석용 Plotly HTML을 생성하는
도구이다.

## XRD 실행

기존 스크립트 실행 방식:

```bash
cd lim
python xrd_plot.py "data/raw.txt" "data/ICDD Card" --origin -o result.html
```

패키지 진입점 실행 방식:

```bash
cd ..
python -m lim.xrd.cli "lim/data/raw.txt" "lim/data/ICDD Card" --origin -o result.html
```

## Edge processor 연동

Edge worker가 자동 processor를 실행하게 하려면 `RIST_PROCESSOR_COMMAND_XRD`에
명령을 등록한다. 명령에는 `{job_root}`, `{input_dir}`, `{processed_dir}`,
`{report_dir}`, `{experiment_code}`, `{job_id}` placeholder를 사용할 수 있다.

```bash
export RIST_PROCESSOR_COMMAND_XRD='python -m lim.xrd.cli "{input_dir}/raw.txt" "{input_dir}/ICDD Card" -o "{processed_dir}/xrd.html"'
```

보고서 생성에는 `processed` 폴더 아래의 구조화 JSON이 필요하므로, 실제 운영용
processor는 HTML과 함께 `analysis-result.json`을 생성해야 한다.
