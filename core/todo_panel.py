"""Lightweight Qt list selector and checklist for the existing content panel."""
from PyQt6.QtCore import Qt,pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QListWidget,QListWidgetItem,QLineEdit,QInputDialog,QMessageBox,QMenu


def _theme(widget,palette):
    if palette is None:return
    widget.setStyleSheet(f"""
        QLabel {{color:{palette.TEXT_MED}; border:none; background:transparent;}}
        QListWidget, QLineEdit {{background:{palette.DARK}; color:{palette.TEXT}; border:1px solid {palette.BORDER}; border-radius:3px; padding:5px;}}
        QListWidget::item {{padding:5px;}}
        QListWidget::item:selected {{background:{palette.PRI_GHO}; color:{palette.PRI};}}
        QPushButton {{background:{palette.PANEL2}; color:{palette.PRI}; border:1px solid {palette.BORDER}; border-radius:3px; padding:4px 7px;}}
        QPushButton:hover {{border-color:{palette.PRI};}}
    """)


class TodoLists(QWidget):
    opened=pyqtSignal(str)
    changed=pyqtSignal()

    def __init__(self,store,parent=None,palette=None):
        super().__init__(parent);_theme(self,palette);self.store=store
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(4)
        header=QHBoxLayout();header.addWidget(QLabel('TO-DO LISTS'));header.addStretch()
        self.create_button=QPushButton('+');self.create_button.setFixedWidth(28)
        self.create_button.setToolTip('Create a to-do list');self.create_button.clicked.connect(self.create)
        header.addWidget(self.create_button);layout.addLayout(header)
        self.names=QListWidget();self.names.setFont(QFont('Courier New',10));self.names.setMaximumHeight(125)
        self.names.itemClicked.connect(lambda item:self.opened.emit(item.data(Qt.ItemDataRole.UserRole)))
        self.names.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.names.customContextMenuRequested.connect(self.menu)
        layout.addWidget(self.names);self.refresh()

    def refresh(self,active=None):
        selected=self.names.currentItem()
        if active is None:active=selected.data(Qt.ItemDataRole.UserRole) if selected else ''
        self.names.clear()
        try:
            for row in self.store.lists():
                item=QListWidgetItem(row['title']);item.setData(Qt.ItemDataRole.UserRole,row['id'])
                done=sum(bool(task['done']) for task in row['tasks'])
                item.setToolTip(f"{done}/{len(row['tasks'])} completed — right-click to rename or delete list")
                self.names.addItem(item)
                if row['id']==active:self.names.setCurrentItem(item)
        except (OSError,ValueError,KeyError) as exc:self.error(exc)

    def error(self,exc):QMessageBox.warning(self,'To-do lists',str(exc))

    def create(self):
        title,ok=QInputDialog.getText(self,'New to-do list','List name:')
        if not ok or not title.strip():return
        try:
            list_id=self.store.create(title);self.refresh(list_id);self.changed.emit();self.opened.emit(list_id)
        except (OSError,ValueError) as exc:self.error(exc)

    def menu(self,point):
        item=self.names.itemAt(point)
        if not item:return
        list_id=item.data(Qt.ItemDataRole.UserRole);menu=QMenu(self)
        rename=menu.addAction('Rename list');delete=menu.addAction('Delete list')
        action=menu.exec(self.names.viewport().mapToGlobal(point))
        try:
            if action==rename:
                title,ok=QInputDialog.getText(self,'Rename to-do list','List name:',text=item.text())
                if not ok or not title.strip():return
                self.store.rename(list_id,title);self.refresh();self.changed.emit();self.opened.emit(list_id)
            elif action==delete:
                answer=QMessageBox.question(self,'Delete to-do list','Delete this list and its tasks?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
                if answer!=QMessageBox.StandardButton.Yes:return
                self.store.delete(list_id);self.refresh();self.changed.emit();self.opened.emit('')
        except (OSError,ValueError) as exc:self.error(exc)


class TodoPanel(QWidget):
    changed=pyqtSignal()

    def __init__(self,store,parent=None,palette=None):
        super().__init__(parent);_theme(self,palette);self.store=store;self.list_id=''
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        row=QHBoxLayout();self.input=QLineEdit();self.input.setPlaceholderText('Add a task to this list…')
        self.input.returnPressed.connect(self.add);row.addWidget(self.input)
        self.add_button=QPushButton('ADD');self.add_button.clicked.connect(self.add);row.addWidget(self.add_button);layout.addLayout(row)
        self.tasks=QListWidget();self.tasks.setFont(QFont('Courier New',11));self.tasks.setMinimumHeight(65)
        self.tasks.itemChanged.connect(self.toggle);self.tasks.itemDoubleClicked.connect(self.edit)
        layout.addWidget(self.tasks)
        controls=QHBoxLayout();self.progress=QLabel('');controls.addWidget(self.progress);controls.addStretch()
        self.edit_button=QPushButton('EDIT');self.edit_button.clicked.connect(self.edit)
        self.delete_button=QPushButton('DELETE TASK');self.delete_button.clicked.connect(self.delete)
        controls.addWidget(self.edit_button);controls.addWidget(self.delete_button);layout.addLayout(controls)
        self.hide()

    def load(self,list_id):
        self.list_id=list_id;self.input.clear();self.refresh()

    def refresh(self):
        row=self.store.get(self.list_id);self.tasks.blockSignals(True)
        try:
            self.tasks.clear()
            for task in (row or {}).get('tasks',[]):
                item=QListWidgetItem(task['text']);item.setData(Qt.ItemDataRole.UserRole,task['id'])
                item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if task['done'] else Qt.CheckState.Unchecked)
                font=item.font();font.setStrikeOut(bool(task['done']));item.setFont(font);self.tasks.addItem(item)
            count=self.tasks.count();done=sum(task['done'] for task in (row or {}).get('tasks',[]))
            self.progress.setText(f'{done}/{count} completed' if count else 'No tasks yet')
        finally:self.tasks.blockSignals(False)

    def change(self,operation):
        try:operation();self.refresh();self.changed.emit();return True
        except (OSError,ValueError) as exc:QMessageBox.warning(self,'To-do list',str(exc));return False

    def add(self):
        text=self.input.text().strip()
        if text and self.change(lambda:self.store.add_task(self.list_id,text)):self.input.clear()

    def toggle(self,item):
        self.change(lambda:self.store.update_task(self.list_id,item.data(Qt.ItemDataRole.UserRole),done=item.checkState()==Qt.CheckState.Checked))

    def edit(self,*_):
        item=self.tasks.currentItem()
        if not item:return
        title,ok=QInputDialog.getText(self,'Edit task','Task:',text=item.text())
        if ok and title.strip():self.change(lambda:self.store.update_task(self.list_id,item.data(Qt.ItemDataRole.UserRole),text=title))

    def delete(self):
        item=self.tasks.currentItem()
        if item:self.change(lambda:self.store.update_task(self.list_id,item.data(Qt.ItemDataRole.UserRole),remove=True))
