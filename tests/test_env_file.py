# main.py
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMessageBox, QVBoxLayout, QWidget


# ---------------------------
# .env loader (sin dependencias)
# ---------------------------
_ENV_LINE_RE = re.compile(
    r"""
    ^\s*
    (?P<key>[A-Za-z_][A-Za-z0-9_]*)
    \s*=\s*
    (?P<val>.*?)
    \s*$
    """,
    re.VERBOSE,
)


def _strip_quotes(value: str) -> str:
    v = value.strip()
    if (len(v) >= 2) and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
        return v[1:-1]
    return v


def load_dotenv(path: Path, *, override: bool = False) -> Dict[str, str]:
    """
    Carga variables tipo .env a os.environ.
    - Soporta comentarios (#) y líneas en blanco.
    - Soporta KEY=VALUE con comillas simples o dobles.
    - No evalúa expresiones (seguridad): no hace "shell expansion".
    """
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"No existe el fichero de configuración: {path}")

    parsed: Dict[str, str] = {}

    for idx, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Quitar comentario al final, respetando comillas de forma simple
        # (suficiente para .env comunes; evita parsear cosas raras).
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
        val = _strip_quotes(m.group("val").strip())

        parsed[key] = val

        if override or (key not in os.environ):
            os.environ[key] = val

    return parsed


# ---------------------------
# Config + validación (seguro)
# ---------------------------
def _to_bool(v: str, *, default: bool = False) -> bool:
    if v is None:
        return default
    s = v.strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _to_int(v: str, *, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _mask_secret(s: Optional[str]) -> str:
    if not s:
        return "(vacío)"
    # Enmascara mostrando solo longitud aproximada
    if len(s) <= 4:
        return "*" * len(s)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


@dataclass(frozen=True)
class AppConfig:
    app_name: str
    env: str  # dev|staging|prod
    debug: bool
    api_base_url: str
    request_timeout_s: int
    api_token: Optional[str]  # secreto


def validate_config() -> Tuple[Optional[AppConfig], Optional[str]]:
    """
    Construye y valida config desde os.environ.
    Devuelve (config, error). No lanza excepciones para poder mostrar UI.
    """
    app_name = os.getenv("APP_NAME", "My PySide6 App")
    env = os.getenv("APP_ENV", "dev").strip().lower()
    debug = _to_bool(os.getenv("APP_DEBUG", "false"), default=False)

    api_base_url = os.getenv("API_BASE_URL", "https://api.example.com").strip()
    request_timeout_s = _to_int(os.getenv("REQUEST_TIMEOUT_S", "10"), default=10)

    api_token = os.getenv("API_TOKEN")  # secreto

    # Validaciones básicas (ajusta a tu caso)
    if env not in {"dev", "staging", "prod"}:
        return None, "APP_ENV debe ser dev|staging|prod."

    if not (api_base_url.startswith("http://") or api_base_url.startswith("https://")):
        return None, "API_BASE_URL debe empezar por http:// o https://"

    if request_timeout_s < 1 or request_timeout_s > 120:
        return None, "REQUEST_TIMEOUT_S debe estar entre 1 y 120."

    # Política típica: en prod, token obligatorio
    if env == "prod" and not api_token:
        return None, "En producción (APP_ENV=prod) es obligatorio definir API_TOKEN."

    return AppConfig(
        app_name=app_name,
        env=env,
        debug=debug,
        api_base_url=api_base_url,
        request_timeout_s=request_timeout_s,
        api_token=api_token,
    ), None


# ---------------------------
# UI PySide6
# ---------------------------
class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.setWindowTitle(config.app_name)

        root = QWidget()
        layout = QVBoxLayout(root)

        layout.addWidget(QLabel(f"APP_ENV: {config.env}"))
        layout.addWidget(QLabel(f"APP_DEBUG: {config.debug}"))
        layout.addWidget(QLabel(f"API_BASE_URL: {config.api_base_url}"))
        layout.addWidget(QLabel(f"REQUEST_TIMEOUT_S: {config.request_timeout_s}"))

        # No mostrar secretos en claro
        layout.addWidget(QLabel(f"API_TOKEN: {_mask_secret(config.api_token)}"))

        self.setCentralWidget(root)


def show_fatal_error(title: str, message: str) -> None:
    m = QMessageBox()
    m.setIcon(QMessageBox.Critical)
    m.setWindowTitle(title)
    m.setText(message)
    m.exec()


def main() -> int:
    # 1) Localiza .env (mismo dir que main.py)
    base_dir = Path(__file__).resolve().parent
    env_file = base_dir / ".env"

    # 2) Carga .env si existe (en prod normalmente no se usa .env, sino variables del sistema)
    if env_file.exists():
        try:
            load_dotenv(env_file, override=False)
        except Exception as e:
            # Inicializa Qt para mostrar el error
            app = QApplication(sys.argv)
            show_fatal_error("Error de configuración", f"No se pudo cargar {env_file}:\n{e}")
            return 2

    # 3) Valida config
    config, err = validate_config()
    app = QApplication(sys.argv)

    if err or config is None:
        show_fatal_error("Configuración inválida", err or "Error desconocido de configuración.")
        return 2

    # 4) Lanza UI
    w = MainWindow(config)
    w.resize(520, 240)
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
