"""Offline UI and persistence check. No accounts, network or user-data writes."""
import ast
import os
from pathlib import Path
import tempfile
import types
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtWidgets import QApplication,QMainWindow,QWidget,QHBoxLayout,QVBoxLayout,QLabel,QSplitter
from PyQt6.QtCore import Qt
from core.activity import activity_notice
from memory.todo_store import TodoStore
from core.todo_panel import TodoLists,TodoPanel
import ui

ROOT=Path(__file__).resolve().parents[1]


def main():
    for text in ('[Agent] goal=Ah, ecco. mode=conversation','[AgentModel] role=LIVE reason=conversation',
        '[AgentRoute] action=open_app','[AgentObserve] result=okay','[AgentVerify] status=verified',
        'SYS: [Agent] plan_steps=3','SYS: Code-only speech filter active; speaker echo is checked before input starts.'):
        assert activity_notice(text) is None,text
    for text in ('SYS: Interrupted — listening...','SYS: Task completed.','ERR: Task failed.',
        'NET: Connection failed — retrying in 6s.','SYS: Task awaiting confirmation.',
        'SYS: Assistant is not connected yet — retry when LISTENING appears.','Gojo: Hello!'):
        assert activity_notice(text,'Gojo')==text,text
    assert activity_notice('[AgentComplete] status=verified')=='SYS: Task completed.'
    assert activity_notice('[AgentComplete] status=failed')=='ERR: Task failed.'
    print('PASS: routing and routine diagnostics hidden; interruption, outcomes, errors, confirmation and chat retained')

    app=QApplication.instance() or QApplication([])
    tree=ast.parse((ROOT/'ui.py').read_text());cls=next(n for n in tree.body if getattr(n,'name','')=='MainWindow')
    scope=dict(vars(ui));scope.update(activity_notice=activity_notice,TodoLists=TodoLists,TodoPanel=TodoPanel)
    for name in ('_build_left_panel','_build_content_panel','_show_content','_show_todo_list','_show_worker_monitor'):
        node=next(n for n in cls.body if getattr(n,'name','')==name)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'ui.py','exec'),scope)
    log_cls=next(n for n in tree.body if getattr(n,'name','')=='LogWidget')
    exec(compile(ast.Module(body=[log_cls],type_ignores=[]),'ui.py','exec'),scope)
    with tempfile.TemporaryDirectory() as directory:
        store=TodoStore(Path(directory)/'todo_lists.json')
        one=store.create('Daily plan');two=store.create('Shopping')
        a=store.add_task(one,'Check microphone');b=store.add_task(one,'Finish report')
        c=store.add_task(two,'Coffee');store.update_task(one,a,done=True)
        store.rename(two,'Weekend shopping');store.update_task(one,b,text='Write the final report')
        assert TodoStore(store.path).get(one)['tasks'][0]['done']
        assert len(store.get(two)['tasks'])==1
        try:store.add_task(one,'   ')
        except ValueError:pass
        else:raise AssertionError('empty task accepted')
        window=QMainWindow();window.resize(1260,780);window._todo_store=store
        for name in ('_select_chat_item','_create_chat','_rename_selected_chat','_delete_selected_chat','_delete_selected_cloud_chat'):
            setattr(window,name,lambda *a:None)
        for name in ('_show_content','_show_todo_list'):
            setattr(window,name,types.MethodType(scope[name],window))
        window.hud=types.SimpleNamespace(glance=lambda *a,**kw:None)
        window._pet=types.SimpleNamespace(workers=lambda *a:None)
        root=QWidget();layout=QHBoxLayout(root);window.setCentralWidget(root)
        left=scope['_build_left_panel'](window);layout.addWidget(left)
        assert left.layout().indexOf(window._todo_sidebar)<left.layout().indexOf(window._bar_cpu)
        window._center_split=QSplitter(Qt.Orientation.Vertical)
        window._center_split.addWidget(QLabel('ARIENX'))
        window._content_panel=scope['_build_content_panel'](window)
        window._center_split.addWidget(window._content_panel);layout.addWidget(window._center_split,1)
        log=scope['LogWidget']();log._ai_name_lc='gojo';layout.addWidget(log)
        window.show();app.processEvents()
        assert window._todo_panel.isHidden() and window._content_panel.isHidden()
        assert [window._todo_sidebar.names.item(i).text() for i in range(2)]==['Daily plan','Weekend shopping']
        window._todo_sidebar.opened.emit(one);app.processEvents()
        assert not window._todo_panel.isHidden() and window._content_display.isHidden()
        assert window._todo_panel.tasks.count()==2 and window._todo_panel.progress.text()=='1/2 completed'
        window._todo_panel.input.setText('Plan tomorrow');window._todo_panel.add_button.click()
        assert len(TodoStore(store.path).get(one)['tasks'])==3
        item=window._todo_panel.tasks.item(1);item.setCheckState(Qt.CheckState.Checked)
        assert store.get(one)['tasks'][1]['done']
        window._todo_panel.tasks.setCurrentRow(2);window._todo_panel.delete_button.click()
        assert len(store.get(one)['tasks'])==2 and len(store.get(two)['tasks'])==1
        window._todo_sidebar.opened.emit(two)
        assert window._todo_panel.tasks.count()==1 and window._todo_panel.tasks.item(0).text()=='Coffee'
        window._show_content('Search results','A result')
        assert window._todo_panel.isHidden() and not window._content_display.isHidden()
        scope['_show_worker_monitor'](window,{'job_id':'check','idle':True,'workers':[{'status':'Completed','heading':'Search'}]})
        assert not window._worker_monitor.isHidden() and window._worker_hide_timer.isActive()
        window._todo_sidebar.opened.emit(one)
        assert window._worker_monitor.isHidden() and not window._worker_hide_timer.isActive()
        import core.todo_panel as widgets
        original_input=widgets.QInputDialog.getText;original_menu=widgets.QMenu.exec
        original_question=widgets.QMessageBox.question
        try:
            widgets.QInputDialog.getText=lambda *a,**kw:('Created through UI',True)
            window._todo_sidebar.create_button.click()
            created=window._todo_panel.list_id
            assert store.get(created)['title']=='Created through UI'
            assert window._todo_sidebar.names.currentItem().data(Qt.ItemDataRole.UserRole)==created
            window._todo_panel.input.setText('Task through UI');window._todo_panel.add_button.click()
            window._todo_panel.tasks.setCurrentRow(0)
            widgets.QInputDialog.getText=lambda *a,**kw:('Edited through UI',True)
            window._todo_panel.edit_button.click()
            assert store.get(created)['tasks'][0]['text']=='Edited through UI'
            widgets.QMenu.exec=lambda menu,*a:menu.actions()[0]
            point=window._todo_sidebar.names.visualItemRect(window._todo_sidebar.names.currentItem()).center()
            window._todo_sidebar.menu(point)
            assert store.get(created)['title']=='Edited through UI'
            widgets.QMenu.exec=lambda menu,*a:menu.actions()[1]
            widgets.QMessageBox.question=lambda *a,**kw:widgets.QMessageBox.StandardButton.Yes
            window._todo_sidebar.menu(point)
            assert store.get(created) is None and window._content_panel.isHidden()
            window._todo_sidebar.opened.emit(one)
        finally:
            widgets.QInputDialog.getText=original_input;widgets.QMenu.exec=original_menu
            widgets.QMessageBox.question=original_question
        from core.agent_core import AgentCore
        agent=AgentCore(logger=log.append_log)
        agent.understand('Ah, ecco.')
        assert not log._queue and not log._typing
        log.append_log('[Agent] goal=secret mode=conversation')
        assert not log._queue and not log._typing
        log.append_log('SYS: Interrupted — listening...');assert log._typing
        log.append_chat('ai','[AgentModel] This is quoted conversation content')
        assert log._queue[-1][0]=='ai'
        if os.environ.get('ARIENX_TODO_PREVIEW'):window.grab().save(os.environ['ARIENX_TODO_PREVIEW'])
        window.close()
        store.update_task(one,a,remove=True);store.delete(two)
        assert len(TodoStore(store.path).lists())==1
        broken=TodoStore(Path(directory)/'broken.json');broken.path.write_text('{bad')
        try:broken.create('Do not replace broken data')
        except ValueError:pass
        else:raise AssertionError('corrupt storage overwritten')
        assert broken.path.read_text()=='{bad'
    print('PASS: lists-only sidebar, bottom-center checklist, add/check/delete, list isolation, reopen persistence, content switching, notice filtering and corrupt-file preservation')


if __name__=='__main__':main()
