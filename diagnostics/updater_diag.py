"""Offline checks using disposable local Git repositories."""
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
from core import updater

def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True,text=True).stdout.strip()

def rejected(fn):
    try: fn()
    except updater.UpdateError: return
    raise AssertionError('Unsafe update was accepted')

def main():
    with tempfile.TemporaryDirectory() as temp:
        seed=Path(temp)/'seed'; seed.mkdir()
        git(seed,'init','-b','main'); git(seed,'config','user.name','Test'); git(seed,'config','user.email','test@example.invalid')
        (seed/'.gitignore').write_text('memory/*.json\n')
        (seed/'app.py').write_text('version=1\n')
        git(seed,'add','.'); git(seed,'commit','-m','initial')
        client=Path(temp)/'client'
        subprocess.run(['git','clone',str(seed),str(client)],check=True,capture_output=True)
        (client/'memory').mkdir(); (client/'memory'/'chats.json').write_text('private')
        with patch.object(updater,'ORIGINS',{str(seed)}):
            assert updater.check(client) is None
            (seed/'app.py').write_text('version=2\n'); git(seed,'add','.'); git(seed,'commit','-m','update')
            update=updater.check(client); assert update and not update['needs_setup']
            (client/'app.py').write_text('local edits\n'); rejected(lambda:updater.apply(update,client))
            git(client,'restore','app.py'); updater.apply(update,client)
            assert (client/'app.py').read_text()=='version=2\n'
            assert (client/'memory'/'chats.json').read_text()=='private'
            (seed/'requirements.txt').write_text('new-dependency\n'); git(seed,'add','.'); git(seed,'commit','-m','dependency change')
            update=updater.check(client); assert update['needs_setup']; rejected(lambda:updater.apply(update,client))
            (seed/'memory').mkdir(); (seed/'memory'/'chats.json').write_text('remote data')
            git(seed,'add','-f','memory/chats.json'); git(seed,'commit','-m','unsafe data')
            rejected(lambda:updater.check(client))
    print('Source updater checks passed: clean update, local-edit refusal, data preservation, dependency and private-data refusal')

if __name__=='__main__': main()
