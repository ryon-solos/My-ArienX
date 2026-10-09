"""Detect accidental voice edits during unrelated updates; no network or recordings.
Run: python3 -m diagnostics.interruption_boundary_diag
A passing fingerprint protects a reviewed source version, not physical audio quality.
"""
import ast
import hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASELINE = {'controller_sha256': '28abf847eebacfb50eda7f6cb9eba02d7397d2a7565e036eb50178b8854f8568', 'methods': {'interrupt': '954a583da16d9edc90e4825f82a6fc2749681cf5ce9275b9def72e13690f1899', '_send_realtime': '6cbceea0d6bb10f139edfd09a372a6d0e21c47d8c0d89f170292d5841fb781b2', '_enqueue_speech_events': 'f38e9ab6eb1e55e67cae46437ca4c20d82d3d7fb3b3ff299adfd9453f0e4d943', '_listen_audio': 'c7019ddd42f08dd7380a3a65e40faf7d7bd020115d3f42fad1f1c871739f8cd1', '_play_audio': 'a5cad5747ba8e1c55d67f6f70e904005fb3115e218bfad897da886d8ff6de1cf', '_receive_audio': '134a6efcbb949a1d8350e0b901bbe1f0f113c7439176914bd741f0a06bf67567', '_watch_input_stall': '6b7a8a43ea60e547a1c902eb299435494d1307b1c3409a4636c62f874ea795a8', '_relay_phone_audio': '90cad46a9de0c8f773eae14ce79be71fc31222cc179314a30e26c279362316a9', 'set_speaking': '278a3b1824ce2a4a526f25a03e49609a797882d3a01716ac8c57c8e2fdd944b3', '_tail_active': '3ed9d166ae4a918fb298431769a54b20f93475d660dc6199b3bce77249a3f730'}}


def main():
    digest=hashlib.sha256((ROOT/'core/interruption.py').read_bytes()).hexdigest()
    assert digest==BASELINE['controller_sha256'],'Interruption controller changed: review VOICE.md and run the voice regression suite before updating this baseline.'
    tree=ast.parse((ROOT/'main.py').read_text())
    cls=next(n for n in tree.body if getattr(n,'name','')=='JarvisLive')
    for name,expected in BASELINE['methods'].items():
        node=next(n for n in cls.body if getattr(n,'name','')==name)
        actual=hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()
        assert actual==expected,f'Voice method {name} changed: this must be an explicitly reviewed voice edit.'
    expected={'CHANNELS':1,'SEND_SAMPLE_RATE':16000,'RECEIVE_SAMPLE_RATE':24000,'CHUNK_SIZE':1024}
    for node in tree.body:
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if getattr(target,'id','') in expected:
                    assert ast.literal_eval(node.value)==expected.pop(target.id),'Voice sample configuration changed'
    assert not expected,'Voice sample constants missing'
    config=next(n for n in cls.body if getattr(n,'name','')=='_build_config')
    # The manual activity configuration must remain an unconditional assignment.
    setups=[node for node in config.body if isinstance(node,ast.Assign)
        and any(isinstance(t,ast.Subscript) and getattr(t.value,'id','')=='cfg'
            and getattr(t.slice,'value',None)=='realtime_input_config' for t in node.targets)]
    assert len(setups)==1,'Manual voice activity setup is missing or conditional'
    detectors=[node for node in ast.walk(setups[0]) if isinstance(node,ast.Call)
        and getattr(node.func,'attr','')=='AutomaticActivityDetection']
    assert len(detectors)==1 and any(k.arg=='disabled' and isinstance(k.value,ast.Constant)
        and k.value.value is True for k in detectors[0].keywords),'Server VAD must stay disabled'
    print('PASS: interruption controller and Live audio wiring match the documented source baseline')


if __name__=='__main__':main()
