import json
from types import SimpleNamespace
import pytest

from app.xrd_workbench.integration import current
from app.xrd_workbench.rist_llm import select_notes
from app.xrd_workbench.core.project import build_project
from app.config import Settings
from app import llm_client
from .synthetic import sample_files


@pytest.mark.parametrize('invalid', [False, True])
def test_local_llm_uses_only_server_settings_and_validates_evidence(tmp_path, monkeypatch, invalid):
    calls = []
    class Client:
        def __init__(self, *args):
            calls.append(args)
        def chat_completion(self, payload):
            evidence = json.loads(payload['messages'][1]['content'])
            if invalid:
                return '{"sample_id":"foreign","notes":[]}', {}
            return json.dumps({'sample_id':evidence['sample_id'],
                'notes':[{'id':c['id'],'text':c['text']} for c in evidence['candidates']]}), {}
        def close(self):
            calls.append('closed')
    monkeypatch.setattr(llm_client, 'LocalLlmClient', Client)
    token = current.set(SimpleNamespace(settings=Settings(storage_root=tmp_path, llm_model='configured-model')))
    try:
        sample = build_project(list(sample_files('SampleA').items()))['samples'][0]
        result = select_notes(sample, {'provider':'ollama', 'base_url':'https://untrusted.invalid', 'model':'client-model'})
        assert calls[0][:2] == ('http://127.0.0.1:8001', 'configured-model')
        assert calls[-1] == 'closed'
        assert result['status'] == ('fallback' if invalid else 'llm')
        assert result['selected_ids'] and result['candidates']
    finally:
        current.reset(token)
