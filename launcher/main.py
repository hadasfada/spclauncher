import json
import os
import platform
import sys
import threading
import traceback
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    pyqtProperty,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from launcher.minecraft import (
    MC_DIR,
    InstallWorker,
    LaunchWorker,
    ModVerifyWorker,
    ServerCheckWorker,
)

# ── Configuration ───────────────────────────────────────────────────

CONFIG_PATH = Path(MC_DIR) / "config.json"
SERVER_URL = "http://104.239.83.122:8000"
SECRET_KEY = "AsiriGizliKimseninBilmemesiGerektigiTokenBuradaysanGelistiricilereKatil!"
CRASH_LOG = Path(MC_DIR) / "crash.log"

# ── Asset paths ─────────────────────────────────────────────────────

_BASE_DIR = (
    Path(sys._MEIPASS)
    if getattr(sys, "frozen", False)
    else Path(__file__).parent.parent
)
ASSETS_DIR = _BASE_DIR / "assets"
LOGO_PATH = ASSETS_DIR / "logo.png"
BG_PATH = ASSETS_DIR / "background.png"
FONT_PATH = ASSETS_DIR / "fonts" / "Unbounded.ttf"

# ── UI constants ────────────────────────────────────────────────────

LOGO_SIZE = 56           # Size of the animated logo (login + main)
ANIM_LOGO_DURATION = 450  # ms for logo fly-between animation


# ── Helpers ─────────────────────────────────────────────────────────


def log(msg):
    """Append a message to the crash log."""
    CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(CRASH_LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return {"username": "", "java_args": "", "ram_mb": 4096}


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


# ── Custom widgets ──────────────────────────────────────────────────


class AnimatedLogo(QLabel):
    """Logo that scales up and glows on hover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scale = 1.0
        self._glow = 0.0
        self._logo_visible = True
        self.setFixedSize(48, 48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pixmap = QPixmap(str(LOGO_PATH))

        self._scale_anim = QPropertyAnimation(self, b"logoScale")
        self._scale_anim.setDuration(300)
        self._scale_anim.setEasingCurve(QEasingCurve.Type.OutBack)

        self._glow_anim = QPropertyAnimation(self, b"glowIntensity")
        self._glow_anim.setDuration(300)
        self._glow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # Qt properties for animation
    logoScale = pyqtProperty(
        float,
        lambda self: self._scale,
        lambda self, v: (setattr(self, "_scale", v), self.update()),
    )
    glowIntensity = pyqtProperty(
        float,
        lambda self: self._glow,
        lambda self, v: (setattr(self, "_glow", v), self.update()),
    )

    def _start_anim(self, scale_end, glow_end):
        self._scale_anim.stop()
        self._scale_anim.setStartValue(self._scale)
        self._scale_anim.setEndValue(scale_end)
        self._scale_anim.start()
        self._glow_anim.stop()
        self._glow_anim.setStartValue(self._glow)
        self._glow_anim.setEndValue(glow_end)
        self._glow_anim.start()

    def enterEvent(self, event):
        self._start_anim(1.15, 1.0)

    def leaveEvent(self, event):
        self._start_anim(1.0, 0.0)

    def paintEvent(self, event):
        if not self._logo_visible:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2

        # Draw glow rings
        if self._glow > 0.01:
            for i in range(4, 0, -1):
                glow_color = QColor(120, 160, 120)
                glow_color.setAlphaF(self._glow * 0.06 * i)
                painter.setBrush(QBrush(glow_color))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(
                    QPoint(int(cx), int(cy)), 20 + i * 4, 20 + i * 4
                )

        # Apply scale transform
        painter.translate(cx, cy)
        painter.scale(self._scale, self._scale)
        painter.translate(-cx, -cy)

        # Draw the logo image
        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(
                (self.width() - scaled.width()) // 2,
                (self.height() - scaled.height()) // 2,
                scaled,
            )
        painter.end()


class ServerStatusWidget(QFrame):
    """Status bar showing server connection state with a pulsing dot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("serverStatus")
        self.setFixedHeight(52)
        self.setMinimumWidth(260)
        self._pulse = 0.0
        self._pulse_dir = 1
        self._connected = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 10, 14, 10)
        layout.setSpacing(10)
        layout.addStretch()

        # Pulse timer for the dot animation
        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._pulse_dot)
        self._dot_timer.start(50)

        self.status_text = QLabel("")
        self.status_text.setObjectName("serverStatusText")
        layout.addWidget(self.status_text)
        layout.addStretch()

    def set_connected(self, connected):
        self._connected = connected
        self.status_text.setText(
            "Sunucu bağlantısı başarılı!" if connected else "Sunucu bağlantısı başarısız."
        )
        self.update()

    def _pulse_dot(self):
        self._pulse += 0.08 * self._pulse_dir
        if self._pulse >= 1.0:
            self._pulse_dir = -1
        elif self._pulse <= 0.0:
            self._pulse_dir = 1
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        dot_color = QColor(100, 160, 100) if self._connected else QColor(180, 60, 60)
        dot_color.setAlpha(max(0, min(255, int(150 + 105 * self._pulse))))

        painter.setBrush(QBrush(dot_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(1, 4, 10, 10)
        painter.end()


class OptionsPanel(QFrame):
    """Slide-in panel for Java arguments and RAM settings."""

    def __init__(self, config, on_logout, parent=None):
        super().__init__(parent)
        self.setObjectName("optionsPanel")
        self.setFixedSize(340, 380)
        self.hide()
        self.config = config
        self.on_logout = on_logout

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(0)

        # Title
        title = QLabel("Ayarlar")
        title.setObjectName("optionsTitle")
        layout.addWidget(title)
        layout.addSpacing(24)

        # Java arguments field
        java_label = QLabel("Java Argümanları")
        java_label.setObjectName("fieldLabel")
        layout.addWidget(java_label)
        layout.addSpacing(8)

        self.java_input = QTextEdit()
        self.java_input.setObjectName("javaInput")
        self.java_input.setFixedHeight(68)
        self.java_input.setPlainText(config.get("java_args", ""))
        layout.addWidget(self.java_input)
        layout.addSpacing(16)

        # RAM spinner
        ram_label = QLabel("Tahsis Edilen RAM (MB)")
        ram_label.setObjectName("fieldLabel")
        layout.addWidget(ram_label)
        layout.addSpacing(8)

        self.ram_spin = QSpinBox()
        self.ram_spin.setObjectName("ramSpin")
        self.ram_spin.setRange(512, 32768)
        self.ram_spin.setSingleStep(512)
        self.ram_spin.setValue(config.get("ram_mb", 4096))
        self.ram_spin.setSuffix(" MB")
        layout.addWidget(self.ram_spin)
        layout.addSpacing(24)

        # Buttons
        save_btn = QPushButton("Kaydet")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(self._save)
        layout.addWidget(save_btn)
        layout.addSpacing(10)

        logout_btn = QPushButton("Çıkış Yap")
        logout_btn.setObjectName("logoutBtn")
        logout_btn.clicked.connect(self._logout)
        layout.addWidget(logout_btn)
        layout.addStretch()

    def _save(self):
        self.config["java_args"] = self.java_input.toPlainText().strip()
        self.config["ram_mb"] = self.ram_spin.value()
        save_config(self.config)
        self.hide()

    def _logout(self):
        self.hide()
        self.on_logout()

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.java_input.setPlainText(self.config.get("java_args", ""))
            self.ram_spin.setValue(self.config.get("ram_mb", 4096))
            self.show()
            self.raise_()


class MinecraftButton(QPushButton):
    """Styled button used for primary/secondary actions."""

    def __init__(self, text, primary=True, parent=None):
        super().__init__(text, parent)
        self.setObjectName("playBtn" if primary else "secondaryBtn")
        self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


# ── Login screen ────────────────────────────────────────────────────


class LoginWindow(QWidget):
    def __init__(self, config, on_login):
        super().__init__()
        self.config = config
        self.on_login = on_login
        self._init_ui()

    def _init_ui(self):
        # Background
        self._bg_label = QLabel(self)
        self._bg_label.setPixmap(QPixmap(str(BG_PATH)))
        self._bg_label.setScaledContents(True)
        self._bg_label.lower()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Centered card
        self._card = QFrame(self)
        self._card.setObjectName("loginCard")
        self._card.setFixedSize(440, 420)

        layout = QVBoxLayout(self._card)
        layout.setContentsMargins(24, 36, 24, 36)
        layout.setSpacing(20)

        self._logo = AnimatedLogo()
        self._logo.setFixedSize(LOGO_SIZE, LOGO_SIZE)

        self._title = QLabel("SpecterCraft'a hoş geldiniz")
        self._title.setObjectName("loginTitle")

        self._subtitle = QLabel("Lütfen bir kullanıcı adı giriniz")
        self._subtitle.setObjectName("loginSubtitle")

        self.username_input = QLineEdit()
        self.username_input.setObjectName("usernameInput")
        self.username_input.setPlaceholderText("Kullanıcı adı...")
        self.username_input.setFixedHeight(48)
        self.username_input.setText(self.config.get("username", ""))
        self.username_input.returnPressed.connect(self._login)

        self._login_btn = MinecraftButton("Giriş Yap")
        self._login_btn.clicked.connect(self._login)

        layout.addStretch()
        layout.addWidget(self._logo, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._subtitle, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(10)
        layout.addWidget(self.username_input)
        layout.addWidget(self._login_btn)
        layout.addStretch()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._bg_label.resize(self.size())
        self._center_card()

    def _center_card(self):
        self._card.move(
            (self.width() - self._card.width()) // 2,
            (self.height() - self._card.height()) // 2,
        )

    def showEvent(self, event):
        super().showEvent(event)
        self._center_card()
        self._animate_elements()

    def _animate_elements(self):
        """Grow-in animation for the card and its children."""
        self._grow_group = QParallelAnimationGroup()
        cw, ch = self._card.width(), self._card.height()
        cx, cy = (self.width() - cw) // 2, (self.height() - ch) // 2

        # Card grows from center
        card_anim = QPropertyAnimation(self._card, b"geometry")
        card_anim.setDuration(400)
        card_anim.setStartValue(QRect(cx + cw // 2, cy + ch // 2, 0, 0))
        card_anim.setEndValue(QRect(cx, cy, cw, ch))
        card_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._grow_group.addAnimation(card_anim)

        # Each child widget grows from its center
        for widget in [
            self._logo, self._title, self._subtitle,
            self.username_input, self._login_btn,
        ]:
            target = widget.geometry()
            start = QRect(
                target.x() + target.width() // 2,
                target.y() + target.height() // 2,
                0, 0,
            )
            anim = QPropertyAnimation(widget, b"geometry")
            anim.setDuration(350)
            anim.setStartValue(start)
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._grow_group.addAnimation(anim)

        self._grow_group.start()

    def _login(self):
        username = self.username_input.text().strip()
        if username:
            self.config["username"] = username
            save_config(self.config)
            self.on_login(username)


# ── Main screen ─────────────────────────────────────────────────────


class MainWindow(QWidget):
    def __init__(self, config, username, on_logout, parent=None):
        super().__init__(parent)
        self.config = config
        self.username = username
        self.on_logout = on_logout

        self.install_worker = None
        self.launch_worker = None
        self.verify_worker = None
        self._mc_process = None

        self._init_ui()

    def _init_ui(self):
        # Background
        self._bg_label = QLabel(self)
        self._bg_label.setPixmap(QPixmap(str(BG_PATH)))
        self._bg_label.setScaledContents(True)
        self._bg_label.lower()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Top bar: user badge + settings button ──────────────────
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(24, 20, 24, 0)

        user_badge = QFrame()
        user_badge.setObjectName("userBadge")
        user_badge.setFixedHeight(56)
        badge_layout = QHBoxLayout(user_badge)
        badge_layout.setContentsMargins(12, 8, 16, 8)
        badge_layout.setSpacing(12)

        logo = QLabel()
        logo.setFixedSize(40, 40)
        logo.setPixmap(
            QPixmap(str(LOGO_PATH)).scaled(
                40, 40,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        logo.setStyleSheet("background: transparent; border: none;")
        badge_layout.addWidget(logo)

        greeting = QLabel(f"İyi günler {self.username}")
        greeting.setObjectName("greetingLabel")
        badge_layout.addWidget(greeting)

        top_bar.addWidget(user_badge)
        top_bar.addStretch()

        self.options_btn = QPushButton()
        self.options_btn.setObjectName("optionsBtn")
        self.options_btn.setFixedSize(40, 40)
        self.options_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.options_btn.setIcon(QIcon(str(ASSETS_DIR / "gear.png")))
        self.options_btn.setIconSize(QSize(20, 20))
        self.options_btn.clicked.connect(self._toggle_options)
        top_bar.addWidget(self.options_btn)
        layout.addLayout(top_bar)

        # ── Content: status + progress + bottom row ───────────────
        content = QVBoxLayout()
        content.setContentsMargins(24, 20, 24, 24)
        content.setSpacing(0)

        # Right column: status label + progress bar
        right_col = QVBoxLayout()
        right_col.setSpacing(12)
        right_col.addStretch()

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("installProgress")
        self.progress_bar.setFixedSize(260, 6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 1)
        right_col.addWidget(self.progress_bar, alignment=Qt.AlignmentFlag.AlignRight)
        content.addLayout(right_col)

        # Bottom row: server status + play button
        bottom_row = QHBoxLayout()
        self.server_status = ServerStatusWidget()
        bottom_row.addWidget(self.server_status)
        bottom_row.addStretch()

        self.play_btn = MinecraftButton("Oynat")
        self.play_btn.setFixedSize(260, 52)
        self.play_btn.clicked.connect(self._on_play)
        bottom_row.addWidget(self.play_btn)
        content.addLayout(bottom_row)

        layout.addLayout(content)

        # Options panel (overlaid)
        self.options_panel = OptionsPanel(self.config, self.on_logout, self)
        self.options_panel.setParent(self)

        # Start background tasks
        self._start_install()
        self._check_server()

    # ── Options panel ─────────────────────────────────────────────

    def _toggle_options(self):
        self.options_panel.toggle()
        if self.options_panel.isVisible():
            self.options_panel.move(self.width() - self.options_panel.width() - 30, 80)

    # ── Install ───────────────────────────────────────────────────

    def _start_install(self):
        self.play_btn.setEnabled(False)
        self.play_btn.setText("Yükleniyor...")
        self.options_btn.setEnabled(False)
        self.options_panel.hide()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.status_label.setText("Kurulum hazırlanıyor...")

        self.install_worker = InstallWorker(
            server_url=SERVER_URL, secret_key=SECRET_KEY
        )
        self.install_worker.progress.connect(self._on_install_progress)
        self.install_worker.finished.connect(self._on_install_done)
        self.install_worker.start()

    def _check_server(self):
        self._server_checker = ServerCheckWorker(SERVER_URL, SECRET_KEY)
        self._server_checker.check_result.connect(
            lambda c: self.server_status.set_connected(c)
        )
        self._server_checker.start()

    def _on_install_progress(self, status, prog, max_val):
        if max_val > 0:
            self.progress_bar.setMaximum(max_val)
        if prog >= 0:
            self.progress_bar.setValue(prog)
        if status:
            self.status_label.setText(status)

    def _on_install_done(self):
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.install_worker = None
        self.status_label.setText("")
        self.play_btn.setEnabled(True)
        self.play_btn.setText("Oynat")
        self.options_btn.setEnabled(True)

    # ── Play / verify / launch ────────────────────────────────────

    def _on_play(self):
        log("_on_play: clicked")

        if SERVER_URL and SECRET_KEY:
            # Verify mods against server before launching
            log("_on_play: starting verify")
            self.play_btn.setEnabled(False)
            self.play_btn.setText("Doğrulanıyor...")
            self.options_btn.setEnabled(False)
            self.options_panel.hide()
            self.status_label.setText("Modlar doğrulanıyor...")

            self.verify_worker = ModVerifyWorker(SERVER_URL, SECRET_KEY)
            self.verify_worker.verify_result.connect(self._on_verify_result)
            self.verify_worker.start()
        else:
            log("_on_play: no server, launching directly")
            self._launch_game()

    def _on_verify_result(self, ok, reason):
        log(f"_on_verify_result: ok={ok} reason={reason}")
        self.verify_worker = None

        if ok:
            self._launch_game()
            return

        if reason == "unreachable":
            log("_on_verify_result: server unreachable, launching anyway")
            self._launch_game()
            return

        # Mods don't match — tell the user to fix them
        self.play_btn.setEnabled(True)
        self.play_btn.setText("Oynat")
        self.options_btn.setEnabled(True)
        self.status_label.setText(
            "Modlar sunucuyla eşleşmiyor, modlarınızı doğrulayın"
        )

    def _launch_game(self):
        log(
            f"_launch_game: username={self.username} "
            f"ram={self.config.get('ram_mb', 4096)} "
            f"java_args={self.config.get('java_args', '')}"
        )

        self.play_btn.setEnabled(False)
        self.play_btn.setText("Başlatılıyor...")
        self.status_label.setText("Minecraft başlatılıyor...")

        self.launch_worker = LaunchWorker(
            self.username,
            self.config.get("java_args", ""),
            self.config.get("ram_mb", 4096),
        )
        self.launch_worker.process_started.connect(self._on_process_started)
        self.launch_worker.process_exited.connect(self._on_process_exited)
        self.launch_worker.error.connect(self._on_launch_error)
        log("_launch_game: starting LaunchWorker")
        self.launch_worker.start()

    # ── Process state callbacks ───────────────────────────────────

    def _on_process_started(self, process):
        log(f"_on_process_started: pid={process.pid if process else 'None'}")
        self._mc_process = process
        self._set_running(True)

    def _on_process_exited(self):
        log("_on_process_exited")
        self._mc_process = None
        self._set_running(False)
        self.status_label.setText("Minecraft kapatıldı")
        QTimer.singleShot(3000, lambda: self.status_label.setText(""))

    def _on_launch_error(self, msg):
        log(f"_on_launch_error: {msg}")
        self._mc_process = None
        self._set_running(False)
        self.status_label.setText(f"Hata: {msg}")

    def _set_running(self, running):
        """Toggle UI between 'playing' and 'idle' states."""
        if running:
            self.play_btn.setText("Durdur")
            self.play_btn.setObjectName("killBtn")
            self.options_btn.setEnabled(False)
            self.options_panel.hide()
            self.play_btn.clicked.disconnect()
            self.play_btn.clicked.connect(self._on_kill)
        else:
            self.play_btn.setText("Oynat")
            self.play_btn.setObjectName("playBtn")
            self.options_btn.setEnabled(True)
            self.play_btn.clicked.disconnect()
            self.play_btn.clicked.connect(self._on_play)

        self.play_btn.setEnabled(True)
        self.play_btn.style().unpolish(self.play_btn)
        self.play_btn.style().polish(self.play_btn)

    def _on_kill(self):
        if self.launch_worker:
            self.launch_worker.kill()
            self._mc_process = None
            self._set_running(False)
            self.status_label.setText("Minecraft durduruldu")
            QTimer.singleShot(3000, lambda: self.status_label.setText(""))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._bg_label.resize(self.size())
        if self.options_panel.isVisible():
            self.options_panel.move(self.width() - self.options_panel.width() - 30, 68)


# ── Main window & app ───────────────────────────────────────────────


class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.setWindowTitle("SpecterCraft")
        self.setWindowIcon(QIcon(str(LOGO_PATH)))
        self.setMinimumSize(800, 520)
        self.resize(900, 560)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.login_window = LoginWindow(self.config, self._on_login)
        self.stack.addWidget(self.login_window)

        self.main_window = None
        self._floating_logo = None
        self._logo_anim = None

        self._apply_stylesheet()

        if self.config.get("username"):
            self._show_main(self.config["username"])

    # ── Navigation ────────────────────────────────────────────────

    def _on_login(self, username):
        self.config["username"] = username
        save_config(self.config)
        self._animate_logo_to_main(username)

    def _on_logout(self):
        self.config["username"] = ""
        save_config(self.config)
        self._animate_logo_to_login()

    def _show_login(self):
        self.login_window = LoginWindow(self.config, self._on_login)
        self.stack.addWidget(self.login_window)
        self.stack.setCurrentWidget(self.login_window)
        self.login_window._center_card()

    def _show_main(self, username):
        self.main_window = MainWindow(self.config, username, self._on_logout)
        self.stack.addWidget(self.main_window)
        self.stack.setCurrentWidget(self.main_window)

    # ── Logo fly-between animation ────────────────────────────────

    def _animate_logo_to_main(self, username):
        self.login_window._logo.hide()
        self._show_main(username)
        self._animate_logo(
            start_widget=self.login_window._logo,
            end_widget=self.main_window.findChild(AnimatedLogo),
            on_finished=lambda: self._finish_logo_anim(self.main_window),
        )

    def _animate_logo_to_login(self):
        self._show_login()
        self._animate_logo(
            start_widget=(
                self.main_window.findChild(AnimatedLogo) if self.main_window else None
            ),
            end_widget=self.login_window._logo,
            on_finished=lambda: self._finish_logo_anim(self.login_window),
            end_card=self.login_window._card,
        )

    def _animate_logo(self, start_widget, end_widget, on_finished, end_card=None):
        """Animate the logo flying from one widget to another."""
        if not start_widget or not end_widget:
            on_finished()
            return

        # Capture start position
        start_g = start_widget.mapToGlobal(QPoint(0, 0))
        start_widget._logo_visible = False
        start_widget.update()

        # Ensure end widget has up-to-date geometry
        if end_card:
            end_card.updateGeometry()
        QApplication.processEvents()

        # Capture end position
        end_g = end_widget.mapToGlobal(QPoint(0, 0))
        end_widget._logo_visible = False
        end_widget.update()

        # Convert global positions to local coordinates
        start_in = self.mapFromGlobal(start_g)
        end_in = self.mapFromGlobal(end_g)
        start_rect = QRect(start_in.x(), start_in.y(), LOGO_SIZE, LOGO_SIZE)
        end_rect = QRect(end_in.x(), end_in.y(), LOGO_SIZE, LOGO_SIZE)

        # Create the floating logo label that animates between positions
        self._floating_logo = QLabel(self)
        self._floating_logo.setPixmap(QPixmap(str(LOGO_PATH)))
        self._floating_logo.setFixedSize(LOGO_SIZE, LOGO_SIZE)
        self._floating_logo.setScaledContents(True)
        self._floating_logo.setGeometry(start_rect)
        self._floating_logo.raise_()
        self._floating_logo.show()

        self._logo_anim = QPropertyAnimation(self._floating_logo, b"geometry")
        self._logo_anim.setDuration(ANIM_LOGO_DURATION)
        self._logo_anim.setStartValue(start_rect)
        self._logo_anim.setEndValue(end_rect)
        self._logo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._logo_anim.finished.connect(on_finished)
        self._logo_anim.start()

    def _finish_logo_anim(self, target_widget):
        """Restore the logo visibility in the target widget and clean up."""
        if target_widget:
            logo = target_widget.findChild(AnimatedLogo)
            if logo:
                logo._logo_visible = True
                logo.update()
        if self._floating_logo:
            self._floating_logo.deleteLater()
            self._floating_logo = None

    # ── Stylesheet ────────────────────────────────────────────────

    def _apply_stylesheet(self):
        self.setStyleSheet("""
            /* ── Global ─────────────────────────────────────── */
            * { font-family: 'Unbounded', 'Helvetica Neue', 'Segoe UI', Arial, sans-serif; }
            QWidget { background-color: transparent; color: #d8d8d8; }

            /* ── Login card ─────────────────────────────────── */
            #loginCard { background: rgba(15, 15, 20, 180); border: 1px solid rgba(80, 90, 80, 80); border-radius: 16px; }
            #loginTitle { font-size: 16px; font-weight: 700; color: #f0f0f0; }
            #loginSubtitle { font-size: 14px; color: #c8c8c8; }

            /* ── Input fields ────────────────────────────────── */
            #usernameInput { background: rgba(20, 20, 25, 180); border: 2px solid rgba(80, 90, 80, 120); border-radius: 10px; padding: 0 16px; font-size: 15px; color: #f0f0f0; selection-background-color: #5a7a5a; }
            #usernameInput:focus { border: 2px solid #6a9a6a; }
            #usernameInput:hover { border: 2px solid rgba(90, 120, 90, 160); }

            /* ── Buttons ─────────────────────────────────────── */
            QPushButton { font-family: 'Unbounded', 'Helvetica Neue', 'Segoe UI', Arial, sans-serif; }

            #playBtn { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4a6a4a, stop:1 #5a7a5a); color: white; border: none; border-radius: 10px; font-size: 17px; font-weight: 700; letter-spacing: 1px; text-align: center; }
            #playBtn:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5a8a5a, stop:1 #6a9a6a); }
            #playBtn:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3a5a3a, stop:1 #4a6a4a); }
            #playBtn:disabled { background: rgba(40, 45, 40, 180); color: #6a7a6a; }

            #killBtn { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7a3a3a, stop:1 #8a4545); color: white; border: none; border-radius: 10px; font-size: 17px; font-weight: 700; letter-spacing: 1px; text-align: center; }
            #killBtn:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8a4a4a, stop:1 #9a5555); }
            #killBtn:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6a2a2a, stop:1 #7a3535); }

            #secondaryBtn { background: rgba(20, 20, 25, 180); color: #c0c0c0; border: 2px solid rgba(80, 90, 80, 120); border-radius: 10px; font-size: 14px; font-weight: 600; }
            #secondaryBtn:hover { border: 2px solid #6a9a6a; color: #ffffff; }

            /* ── Server status ──────────────────────────────── */
            #serverStatus { background: rgba(20, 20, 25, 200); border: 2px solid rgba(80, 90, 80, 120); border-radius: 10px; }
            #serverStatusText { font-size: 15px; font-weight: 600; color: #d0d0d0; }

            /* ── Top bar ────────────────────────────────────── */
            #greetingLabel { font-size: 20px; font-weight: 700; color: #f0f0f0; }
            #statusLabel { font-size: 12px; color: #a0a0a0; }
            #optionsBtn { background: rgba(20, 20, 25, 180); border: 2px solid rgba(80, 90, 80, 120); border-radius: 10px; color: #a0a0a0; font-size: 20px; }
            #optionsBtn:hover { border-color: #6a9a6a; color: #6a9a6a; }
            #userBadge { background: rgba(20, 20, 25, 180); border: 2px solid rgba(80, 90, 80, 120); border-radius: 10px; }

            /* ── Progress bar ───────────────────────────────── */
            #installProgress { border: none; border-radius: 3px; background: transparent; }
            #installProgress::chunk { border-radius: 3px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4a6a4a, stop:1 #5a7a5a); }

            /* ── Options panel ──────────────────────────────── */
            #optionsPanel { background: rgba(18, 18, 22, 220); border: 1px solid rgba(80, 90, 80, 100); border-radius: 14px; }
            #optionsTitle { background: transparent; font-size: 18px; font-weight: 700; color: #f0f0f0; }
            #fieldLabel { background: transparent; font-size: 12px; font-weight: 600; color: #a0a0a0; text-transform: uppercase; letter-spacing: 1px; }
            #javaInput { background: rgba(12, 12, 16, 200); border: 2px solid rgba(80, 90, 80, 100); border-radius: 8px; padding: 8px 12px; font-size: 13px; font-family: 'Cascadia Code', 'Consolas', monospace; color: #c0d0c0; selection-background-color: #5a7a5a; }
            #javaInput:focus { border: 2px solid #6a9a6a; }
            #ramSpin { background: rgba(12, 12, 16, 200); border: 2px solid rgba(80, 90, 80, 100); border-radius: 8px; padding: 6px 12px; font-size: 13px; color: #f0f0f0; min-width: 120px; }
            #ramSpin:focus, #ramSpin:on { border: 2px solid #6a9a6a; }
            #ramSpin::up-button, #ramSpin::down-button { background: rgba(60, 65, 60, 180); border: none; width: 20px; }
            #ramSpin::up-button:hover, #ramSpin::down-button:hover { background: #5a7a5a; }
            #saveBtn { background: #5a7a5a; color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: 700; padding: 10px; }
            #saveBtn:hover { background: #6a9a6a; }
            #saveBtn:pressed { background: #4a6a4a; }
            #logoutBtn { background: rgba(120, 50, 50, 180); color: #d0a0a0; border: 1px solid rgba(140, 70, 70, 120); border-radius: 8px; font-size: 13px; font-weight: 600; padding: 8px; }
            #logoutBtn:hover { background: rgba(150, 60, 60, 200); color: #f0c0c0; border-color: rgba(170, 80, 80, 160); }
            #logoutBtn:pressed { background: rgba(100, 40, 40, 200); }

            /* ── Scrollbar ──────────────────────────────────── */
            QScrollBar:vertical { background: transparent; width: 8px; border-radius: 4px; }
            QScrollBar::handle:vertical { background: rgba(80, 90, 80, 120); border-radius: 4px; min-height: 30px; }
            QScrollBar::handle:vertical:hover { background: #5a7a5a; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)


# ── Exception hooks ─────────────────────────────────────────────────


def _excepthook(exc_type, exc_value, exc_tb):
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log(msg)
    # Only show QMessageBox from the main thread
    import threading
    if threading.current_thread() is threading.main_thread():
        QMessageBox.critical(None, "SpecterCraft Hata", msg)


def _thread_excepthook(args):
    msg = "".join(
        traceback.format_exception(args.exc_type, args.exc_value, args.traceback)
    )
    log(msg)


# ── Entry point ─────────────────────────────────────────────────────


def main():
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook

    # Force software rendering on Linux to avoid OpenGL segfaults
    if platform.system() == "Linux":
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
        os.environ.setdefault("QT_QUICK_BACKEND", "software")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Load the Unbounded font, fall back to system font
    try:
        font_id = QFontDatabase.addApplicationFont(str(FONT_PATH))
        if font_id != -1:
            font = QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10)
        else:
            font = QFont("Helvetica Neue", 10)
    except Exception:
        font = QFont("Helvetica Neue", 10)
    app.setFont(font)

    launcher = Launcher()
    launcher.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
