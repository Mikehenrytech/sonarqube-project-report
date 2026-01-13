from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from simple_sonarqube_api.client import SonarQubeClient, Project

# ---------------------------
# .env loader (sin dependencias)
# ---------------------------
_ENV_LINE_RE = re.compile(
    r"""^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<val>.*?)\s*$"""
)


def _strip_quotes(value: str) -> str:
    v = value.strip()
    if (len(v) >= 2) and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
        return v[1:-1]
    return v


def load_dotenv(path: Path, *, override: bool = False) -> Dict[str, str]:
    """
    Carga variables tipo .env en os.environ.
    - Comentarios (#) y líneas en blanco.
    - KEY=VALUE con comillas simples o dobles.
    - No hace expansiones tipo shell (más seguro).
    """
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"No existe el fichero .env: {path}")

    parsed: Dict[str, str] = {}

    for idx, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Quitar comentario final respetando comillas (simple)
        in_squote = False
        in_dquote = False
        cleaned_chars = []
        for ch in raw_line:
            if ch == "'" and not in_dquote:
                in_squote = not in_squote
            elif ch == '"' and not in_squote:
                in_dquote = not in_dquote
            if ch == "#" and not in_squote and not in_dquote:
                break
            cleaned_chars.append(ch)
        cleaned = "".join(cleaned_chars).strip()
        if not cleaned:
            continue

        m = _ENV_LINE_RE.match(cleaned)
        if not m:
            raise ValueError(f"Línea inválida en {path} (línea {idx}): {raw_line}")

        key = m.group("key")
        val = _strip_quotes(m.group("val"))

        parsed[key] = val
        if override or (key not in os.environ):
            os.environ[key] = val

    return parsed


# ---------------------------
# Config + validación
# ---------------------------
def _to_int(v: Optional[str], default: int) -> int:
    try:
        return int(v) if v is not None else default
    except Exception:
        return default


@dataclass(frozen=True)
class AppConfig:
    app_name: str
    sonar_url: str
    sonar_token: str
    page_size: int
    env_file: Path


def load_config(env_file: Path) -> Tuple[Optional[AppConfig], Optional[str]]:
    if env_file.exists():
        try:
            load_dotenv(env_file, override=False)
        except Exception as e:
            return None, f"No se pudo cargar {env_file}: {e}"

    app_name = os.getenv("APP_NAME", "SonarQube Reporter").strip()
    sonar_url = os.getenv("SONAR_URL", "").strip().rstrip("/")
    sonar_token = os.getenv("SONAR_TOKEN", "").strip()
    page_size = _to_int(os.getenv("SONAR_PAGE_SIZE"), 200)

    if not sonar_url.startswith(("http://", "https://")):
        return None, "SONAR_URL debe empezar por http:// o https://"
    if not sonar_token:
        return None, "Falta SONAR_TOKEN (no lo guardes en Git)."
    if page_size < 1 or page_size > 500:
        return None, "SONAR_PAGE_SIZE debe estar entre 1 y 500."

    return AppConfig(
        app_name=app_name,
        sonar_url=sonar_url,
        sonar_token=sonar_token,
        page_size=page_size,
        env_file=env_file,
    ), None


# ---------------------------
# SonarQube client (library)
# ---------------------------
#sonarqube_client: SonarQubeClient = SonarQubeClient(load_config()., AppConfig.sonar_token)


# ---------------------------
# Worker thread
# ---------------------------
class FetchWorker(QObject):
    finished = Signal(list)       # list[dict]
    failed = Signal(str)

    def __init__(self, config: AppConfig, sonarqube_client: SonarQubeClient):
        super().__init__()
        self.config = config
        self._sonarqube_client = sonarqube_client

    def run(self) -> None:
        try:
            if self._sonarqube_client.is_authenticated():
                projects = self._sonarqube_client.list_projects_normalized()
                print(projects)
                self.finished.emit(projects)
            else:
                print("Error de autenticación con Sonarqube.")
        except Exception as e:
            self.failed.emit(str(e))


# ---------------------------
# Config dialog (básico)
# ---------------------------
class ConfigDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Configuración")
        self.setModal(True)

        self._config = config

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.sonar_url = QLineEdit(config.sonar_url)
        self.sonar_token = QLineEdit(config.sonar_token)
        self.sonar_token.setEchoMode(QLineEdit.Password)
        self.page_size = QSpinBox()
        self.page_size.setRange(1, 500)
        self.page_size.setValue(config.page_size)

        self.env_path = QLineEdit(str(config.env_file))
        self.env_path.setReadOnly(True)

        form.addRow("SONAR_URL", self.sonar_url)
        form.addRow("SONAR_TOKEN", self.sonar_token)
        form.addRow("SONAR_PAGE_SIZE", self.page_size)
        form.addRow(".env", self.env_path)
        layout.addLayout(form)

        btns = QHBoxLayout()
        self.btn_ok = QPushButton("Aceptar")
        self.btn_cancel = QPushButton("Cancelar")
        btns.addStretch(1)
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)
        layout.addLayout(btns)

        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

    def get_new_config(self) -> AppConfig:
        # Nota: en una app “real” evitarías persistir el token en texto plano.
        return AppConfig(
            app_name=self._config.app_name,
            sonar_url=self.sonar_url.text().strip().rstrip("/"),
            sonar_token=self.sonar_token.text().strip(),
            page_size=int(self.page_size.value()),
            env_file=self._config.env_file,
        )


# ---------------------------
# Main Window
# ---------------------------
class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.setWindowTitle(self.config.app_name)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        self._thread: Optional[QThread] = None
        self._worker: Optional[FetchWorker] = None
        self._sonarqube_client: SonarQubeClient = SonarQubeClient(config.sonar_url, config.sonar_token)

        # Central UI
        central = QWidget()
        root = QVBoxLayout(central)

        header = QHBoxLayout()
        self.lbl_info = QLabel("Proyectos SonarQube")
        self.btn_refresh = QPushButton("Refrescar")
        self.btn_select_all = QPushButton("Seleccionar todos")
        self.btn_select_none = QPushButton("Deseleccionar todos")
        header.addWidget(self.lbl_info)
        header.addStretch(1)
        header.addWidget(self.btn_refresh)
        header.addWidget(self.btn_select_all)
        header.addWidget(self.btn_select_none)
        root.addLayout(header)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Sel", "Key", "Project", "Name", "Revision", "Last analysis date item"])
        self.table.setColumnWidth(0, 50)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        root.addWidget(self.table)

        footer = QHBoxLayout()
        self.btn_individual = QPushButton("Informes individuales")
        self.btn_org = QPushButton("Informe de la Organización")
        footer.addStretch(1)
        footer.addWidget(self.btn_individual)
        footer.addWidget(self.btn_org)
        root.addLayout(footer)

        self.setCentralWidget(central)

        # Menu
        self._build_menu()

        # Signals
        self.btn_refresh.clicked.connect(self.fetch_projects)
        self.btn_select_all.clicked.connect(lambda: self.set_all_checked(True))
        self.btn_select_none.clicked.connect(lambda: self.set_all_checked(False))
        self.btn_individual.clicked.connect(self.on_dummy_individual)
        self.btn_org.clicked.connect(self.on_dummy_org)

        # Primer fetch
        self.fetch_projects()

    def _build_menu(self) -> None:
        menubar = self.menuBar()

        menu_archivos = menubar.addMenu("Archivos")
        act_exit = QAction("Salir", self)
        act_exit.triggered.connect(self.close)
        menu_archivos.addAction(act_exit)

        menu_conf = menubar.addMenu("Configuración")
        act_conf = QAction("Ajustes…", self)
        act_conf.triggered.connect(self.open_config)
        menu_conf.addAction(act_conf)

        menu_info = menubar.addMenu("Información")
        act_about = QAction("Acerca de…", self)
        act_about.triggered.connect(self.open_about)
        menu_info.addAction(act_about)

    def open_about(self) -> None:
        QMessageBox.information(
            self,
            "Acerca de",
            "SonarQube Reporter (MVP)\n"
            "Lista proyectos desde SonarQube y prepara la base para generación de informes.\n\n"
            "Endpoints: /api/components/search?qualifiers=TRK",
        )

    def open_config(self) -> None:
        dlg = ConfigDialog(self.config, self)
        if dlg.exec() == QDialog.Accepted:
            new_cfg = dlg.get_new_config()
            # Validación mínima
            if not new_cfg.sonar_url.startswith(("http://", "https://")):
                QMessageBox.critical(self, "Configuración inválida", "SONAR_URL no es válida.")
                return
            if not new_cfg.sonar_token:
                QMessageBox.critical(self, "Configuración inválida", "SONAR_TOKEN es obligatorio.")
                return

            self.config = new_cfg
            self.status.showMessage("Configuración actualizada. Refrescando…", 3000)
            self.fetch_projects()

    def _clear_table(self) -> None:
        self.table.setRowCount(0)

    def populate_table2(self, projects: List[Dict[str, str]]) -> None:
        self._clear_table()
        self.table.setRowCount(len(projects))

        for row, project in enumerate(projects):
            # Checkbox
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_item.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, chk_item)

            print(f"row: {row}")
            print(f"p: {project}")
            key_item = QTableWidgetItem(project.get("key", ""))
            name_item = QTableWidgetItem(project.get("name", ""))

            self.table.setItem(row, 1, key_item)
            self.table.setItem(row, 2, name_item)

        self.table.resizeColumnsToContents()
        self.status.showMessage(f"Proyectos cargados: {len(projects)}", 5000)

    def populate_table(self, projects: list(Project)) -> None:
        self._clear_table()
        self.table.setRowCount(len(projects))

        for index in range(0, len(projects)):
            # Checkbox
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_item.setCheckState(Qt.Unchecked)
            self.table.setItem(index, 0, chk_item)

            project: Project = projects[index]

            print(f"row: {index}")
            print(f"p: {project}")
            key_item = QTableWidgetItem(project.key)
            name_item = QTableWidgetItem(project.name)
            project_item = QTableWidgetItem(project.project)
            revision_item = QTableWidgetItem(project.revision)
            last_analysis_date_item = QTableWidgetItem(project.last_analysis_date)

            self.table.setItem(index, 1, key_item)
            self.table.setItem(index, 2, name_item)
            self.table.setItem(index, 3, project_item)
            self.table.setItem(index, 4, revision_item)
            self.table.setItem(index, 5, last_analysis_date_item)

        self.table.resizeColumnsToContents()
        self.status.showMessage(f"Proyectos cargados: {len(projects)}", 5000)

    def set_all_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                item.setCheckState(state)

    def get_selected_project_keys(self) -> List[str]:
        keys: List[str] = []
        for row in range(self.table.rowCount()):
            sel_item = self.table.item(row, 0)
            key_item = self.table.item(row, 1)
            if sel_item and key_item and sel_item.checkState() == Qt.Checked:
                keys.append(key_item.text().strip())
        return [k for k in keys if k]

    def on_dummy_individual(self) -> None:
        keys = self.get_selected_project_keys()
        if not keys:
            QMessageBox.warning(self, "Sin selección", "Selecciona al menos un proyecto.")
            return
        QMessageBox.information(
            self,
            "Dummy: Informes individuales",
            f"Generaría un DOCX por proyecto.\n\nSeleccionados ({len(keys)}):\n" + "\n".join(keys[:50]) +
            ("\n…" if len(keys) > 50 else ""),
        )

    def on_dummy_org(self) -> None:
        keys = self.get_selected_project_keys()
        if not keys:
            QMessageBox.warning(self, "Sin selección", "Selecciona al menos un proyecto.")
            return
        QMessageBox.information(
            self,
            "Dummy: Informe de la Organización",
            f"Generaría un DOCX agregado con estadísticas de criticidades.\n\nProyectos incluidos: {len(keys)}",
        )

    def fetch_projects(self) -> None:
        # Evitar múltiples hilos simultáneos
        if self._thread and self._thread.isRunning():
            self.status.showMessage("Ya hay una carga en curso…", 2000)
            return

        self.status.showMessage("Cargando proyectos desde SonarQube…")

        self._thread = QThread()
        self._worker = FetchWorker(self.config, self._sonarqube_client)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_fetch_ok)
        self._worker.failed.connect(self._on_fetch_fail)

        # limpieza
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup_worker)

        self._thread.start()

    def _cleanup_worker(self) -> None:
        self._worker = None
        self._thread = None

    def _on_fetch_ok(self, projects: list) -> None:
        self.populate_table(projects)

    def _on_fetch_fail(self, err: str) -> None:
        QMessageBox.critical(
            self,
            "Error al cargar proyectos",
            "No se pudieron obtener los proyectos.\n\n"
            f"Detalle:\n{err}\n\n"
            f"Revisa SONAR_URL: {self.config.sonar_url} / SONAR_TOKEN y conectividad.",
        )
        self.status.showMessage("Error al cargar proyectos", 5000)


# ---------------------------
# main
# ---------------------------
def main() -> int:
    base_dir = Path(__file__).resolve().parent
    env_file = base_dir / ".env"

    config, err = load_config(env_file)

    app = QApplication(sys.argv)
    if err or config is None:
        QMessageBox.critical(None, "Configuración inválida", err or "Error desconocido.")
        return 2

    w = MainWindow(config)
    w.resize(900, 600)
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
