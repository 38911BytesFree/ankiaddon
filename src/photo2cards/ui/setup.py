"""First-run / settings dialog.

The flow is deliberately: paste key -> we call ListModels -> the dropdown fills.
That single call validates the key *and* discovers which model ids the key can
actually reach, so we never ship a hardcoded model name that has since been
renamed or retired.
"""

from __future__ import annotations

from aqt import mw
from aqt.operations import QueryOp
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    Qt,
    QVBoxLayout,
)
from aqt.utils import openLink, showWarning, tooltip

from ..core.errors import Photo2CardsError
from ..core.gemini import list_models
from .store import get_config, save_config

KEY_URL = "https://aistudio.google.com/apikey"

#: Preferred when present — fast, cheap, and vision-capable. Purely a default for
#: the dropdown; the user can pick anything the API reports.
PREFERRED_SUBSTRINGS = ("flash",)


class SettingsDialog(QDialog):
    def __init__(self, parent=None, *, first_run: bool = False) -> None:
        super().__init__(parent or mw)
        self.setWindowTitle("Photo to Flashcards — Settings")
        self.setMinimumWidth(520)
        self.config = get_config()
        self._models: list[dict] = []

        layout = QVBoxLayout(self)

        if first_run:
            intro = QLabel(
                "<b>Welcome!</b><br><br>"
                "This add-on uses your own Google AI Studio API key, so your usage runs "
                "against your own free quota rather than a shared one. Creating a key is "
                "free and takes about a minute."
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)

        form = QFormLayout()

        # --- key row -------------------------------------------------------
        self.key_edit = QLineEdit(self.config.get("api_key", ""))
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("AIza…")
        self.key_edit.textChanged.connect(self._on_key_changed)

        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        self.show_key = QCheckBox("Show")
        self.show_key.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row.addWidget(self.show_key)
        form.addRow("API key:", key_row)

        get_key = QPushButton("Get a free API key…")
        get_key.clicked.connect(lambda: openLink(KEY_URL))
        form.addRow("", get_key)

        # --- model row -----------------------------------------------------
        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEnabled(False)
        model_row.addWidget(self.model_combo, 1)
        self.verify_btn = QPushButton("Verify key && load models")
        self.verify_btn.clicked.connect(self._verify)
        model_row.addWidget(self.verify_btn)
        form.addRow("Model:", model_row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        form.addRow("", self.status)

        # --- tuning --------------------------------------------------------
        self.edge_spin = QSpinBox()
        self.edge_spin.setRange(512, 4096)
        self.edge_spin.setSingleStep(128)
        self.edge_spin.setValue(int(self.config.get("max_image_edge", 1600)))
        self.edge_spin.setSuffix(" px")
        self.edge_spin.setToolTip(
            "Images are shrunk to this on their longest edge before upload.\n"
            "Lower is faster and cheaper; raise it if dense pages read poorly."
        )
        form.addRow("Max image size:", self.edge_spin)

        self.rpm_spin = QSpinBox()
        self.rpm_spin.setRange(1, 1000)
        self.rpm_spin.setValue(int(self.config.get("requests_per_minute", 15)))
        self.rpm_spin.setToolTip(
            "Requests are spaced out locally to stay under this.\n"
            "Set it to your tier's per-minute limit."
        )
        form.addRow("Requests per minute:", self.rpm_spin)

        self.attach_check = QCheckBox("Store the source photo with each note")
        self.attach_check.setChecked(bool(self.config.get("attach_source_image", True)))
        form.addRow("", self.attach_check)

        layout.addLayout(form)

        warning = QLabel(
            "<i>Your key is stored unencrypted in this add-on's folder, because Anki has "
            "no keychain integration. Don't share your Anki data folder, and revoke the "
            "key in AI Studio if it leaks.</i>"
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.config.get("api_key"):
            self._verify()

    # ------------------------------------------------------------------ #

    def _on_key_changed(self) -> None:
        """A changed key invalidates the model list it produced."""
        self.model_combo.clear()
        self.model_combo.setEnabled(False)
        self._models = []
        self.status.setText("")

    def _verify(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            showWarning("Enter an API key first.", parent=self)
            return

        self.verify_btn.setEnabled(False)
        self.status.setText("Checking key…")

        def work(_col) -> list[dict]:
            return list_models(key)

        def done(models: list[dict]) -> None:
            self.verify_btn.setEnabled(True)
            self._models = models
            self._populate_models()

        def failed(exc: Exception) -> None:
            self.verify_btn.setEnabled(True)
            self.status.setText("")
            if isinstance(exc, Photo2CardsError):
                showWarning(str(exc), parent=self, title="Key check failed")
            else:
                showWarning(f"Unexpected error: {exc}", parent=self)

        QueryOp(parent=self, op=work, success=done).failure(failed).run_in_background()

    def _populate_models(self) -> None:
        self.model_combo.clear()
        if not self._models:
            self.status.setText(
                "The key works, but reports no models that support image input."
            )
            return

        for model in self._models:
            label = model["display_name"]
            if label != model["id"]:
                label = f"{label}  ({model['id']})"
            self.model_combo.addItem(label, model["id"])

        self.model_combo.setEnabled(True)
        self._select_default_model()
        self.status.setText(
            f"Key verified — {len(self._models)} model(s) available."
        )

    def _select_default_model(self) -> None:
        """Keep the saved model if it still exists; otherwise prefer a Flash-class one."""
        saved = (self.config.get("model") or "").strip()
        ids = [m["id"] for m in self._models]

        if saved in ids:
            self.model_combo.setCurrentIndex(ids.index(saved))
            return

        for i, model_id in enumerate(ids):
            if any(s in model_id.lower() for s in PREFERRED_SUBSTRINGS):
                self.model_combo.setCurrentIndex(i)
                return
        self.model_combo.setCurrentIndex(0)

    def _save(self) -> None:
        key = self.key_edit.text().strip()
        model = self.model_combo.currentData()

        if not key:
            showWarning("An API key is required.", parent=self)
            return
        if not model:
            showWarning(
                "Click “Verify key & load models” and pick a model before saving.",
                parent=self,
            )
            return

        self.config.update(
            {
                "backend": "gemini_direct",
                "api_key": key,
                "model": model,
                "max_image_edge": self.edge_spin.value(),
                "requests_per_minute": self.rpm_spin.value(),
                "attach_source_image": self.attach_check.isChecked(),
            }
        )
        save_config(self.config)
        tooltip("Settings saved.", parent=mw)
        self.accept()


def show_settings(parent=None, *, first_run: bool = False) -> bool:
    """Open the dialog. Returns True if the user saved."""
    dialog = SettingsDialog(parent, first_run=first_run)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    return dialog.exec() == QDialog.DialogCode.Accepted
