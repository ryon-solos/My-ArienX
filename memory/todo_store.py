"""Small local to-do lists. Atomic saves; invalid saved data is never overwritten."""
import json
from pathlib import Path
import threading
import uuid
from .memory_manager import get_base_dir

_lock=threading.RLock()


class TodoStore:
    def __init__(self,path=None):
        self.path=Path(path) if path else get_base_dir()/'memory'/'todo_lists.json'

    def lists(self):
        with _lock:
            try:data=json.loads(self.path.read_text(encoding='utf-8'))
            except FileNotFoundError:return []
            def valid(row):
                return (isinstance(row,dict) and isinstance(row.get('id'),str)
                    and isinstance(row.get('title'),str) and isinstance(row.get('tasks'),list)
                    and all(isinstance(task,dict) and isinstance(task.get('id'),str)
                        and isinstance(task.get('text'),str) and isinstance(task.get('done'),bool)
                        for task in row['tasks']))
            if not isinstance(data,list) or not all(valid(row) for row in data):
                raise ValueError('Saved to-do lists are invalid; the file has been preserved.')
            return data

    def get(self,list_id):
        return next((row for row in self.lists() if row['id']==list_id),None)

    def _change(self,operation):
        with _lock:
            data=self.lists();result=operation(data)
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.path)
            return result

    @staticmethod
    def _title(text):
        text=str(text).strip()
        if not text:raise ValueError('Enter a name or task first.')
        return text

    @staticmethod
    def _find(data,list_id):
        row=next((row for row in data if row['id']==list_id),None)
        if row is None:raise ValueError('This to-do list no longer exists.')
        return row

    def create(self,title):
        row={'id':uuid.uuid4().hex,'title':self._title(title),'tasks':[]}
        self._change(lambda data:data.append(row))
        return row['id']

    def rename(self,list_id,title):
        title=self._title(title)
        self._change(lambda data:self._find(data,list_id).update(title=title))

    def delete(self,list_id):
        self._change(lambda data:data.remove(self._find(data,list_id)))

    def add_task(self,list_id,text):
        task={'id':uuid.uuid4().hex,'text':self._title(text),'done':False}
        self._change(lambda data:self._find(data,list_id)['tasks'].append(task))
        return task['id']

    def update_task(self,list_id,task_id,*,text=None,done=None,remove=False):
        def change(data):
            tasks=self._find(data,list_id)['tasks']
            task=next((task for task in tasks if task['id']==task_id),None)
            if task is None:raise ValueError('This task no longer exists.')
            if remove:tasks.remove(task)
            else:
                if text is not None:task['text']=self._title(text)
                if done is not None:task['done']=bool(done)
        self._change(change)
