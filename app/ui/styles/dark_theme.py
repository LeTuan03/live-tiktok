"""Modern dark theme for LiveFlow AI Studio.

Inspired by premium dark UIs with vibrant accent colors,
glassmorphism effects, and smooth visual hierarchy.
"""

# Color palette
COLORS = {
    # Background layers
    "bg_darkest": "#0a0b0f",
    "bg_dark": "#0f1117",
    "bg_medium": "#161823",
    "bg_light": "#1e2030",
    "bg_lighter": "#262940",
    "bg_hover": "#2d3050",
    "bg_active": "#353860",
    "bg_card": "#161823",

    # Accent colors
    "accent": "#6c5ce7",
    "accent_primary": "#6c5ce7",
    "accent_primary_hover": "#7e70f0",
    "accent_primary_dark": "#5a4bd6",
    "accent_secondary": "#00cec9",
    "accent_success": "#00b894",
    "accent_warning": "#fdcb6e",
    "accent_danger": "#e17055",
    "accent_info": "#74b9ff",

    # Text
    "text_primary": "#e4e6f0",
    "text_secondary": "#a0a3b8",
    "text_muted": "#6b6e85",
    "text_disabled": "#454860",
    "text_accent": "#a29bfe",

    # Borders
    "border": "#2a2d45",
    "border_hover": "#3d4070",
    "border_focus": "#6c5ce7",

    # Status
    "status_online": "#00b894",
    "status_offline": "#636e72",
    "status_error": "#e17055",
    "status_warning": "#fdcb6e",
    "status_info": "#74b9ff",

    # Scrollbar
    "scrollbar_bg": "#161823",
    "scrollbar_handle": "#2a2d45",
    "scrollbar_handle_hover": "#3d4070",
}


def get_stylesheet() -> str:
    """Generate the complete application stylesheet."""
    c = COLORS
    return f"""
/* ===== Global ===== */
QWidget {{
    background-color: {c['bg_dark']};
    color: {c['text_primary']};
    font-family: 'Segoe UI', 'Inter', 'Roboto', sans-serif;
    font-size: 13px;
    selection-background-color: {c['accent_primary']};
    selection-color: white;
}}

QMainWindow {{
    background-color: {c['bg_darkest']};
}}

/* ===== Menu Bar ===== */
QMenuBar {{
    background-color: {c['bg_darkest']};
    border-bottom: 1px solid {c['border']};
    padding: 2px 0;
}}
QMenuBar::item {{
    padding: 6px 12px;
    border-radius: 4px;
    margin: 2px;
}}
QMenuBar::item:selected {{
    background-color: {c['bg_hover']};
}}
QMenu {{
    background-color: {c['bg_medium']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 8px 32px 8px 16px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {c['accent_primary']};
    color: white;
}}
QMenu::separator {{
    height: 1px;
    background-color: {c['border']};
    margin: 4px 8px;
}}

/* ===== Sidebar Navigation ===== */
QListWidget#sidebar {{
    background-color: {c['bg_darkest']};
    border: none;
    border-right: 1px solid {c['border']};
    outline: none;
    padding: 8px 4px;
}}
QListWidget#sidebar::item {{
    padding: 12px 16px;
    border-radius: 8px;
    margin: 2px 4px;
    color: {c['text_secondary']};
    font-size: 13px;
}}
QListWidget#sidebar::item:hover {{
    background-color: {c['bg_hover']};
    color: {c['text_primary']};
}}
QListWidget#sidebar::item:selected {{
    background-color: {c['accent_primary']};
    color: white;
    font-weight: 600;
}}

/* ===== Buttons ===== */
QPushButton {{
    background-color: {c['bg_lighter']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 8px 20px;
    font-weight: 500;
    min-height: 20px;
}}
QPushButton:hover {{
    background-color: {c['bg_hover']};
    border-color: {c['border_hover']};
}}
QPushButton:pressed {{
    background-color: {c['bg_active']};
}}
QPushButton:disabled {{
    background-color: {c['bg_medium']};
    color: {c['text_disabled']};
    border-color: {c['bg_medium']};
}}
QPushButton#primaryButton {{
    background-color: {c['accent_primary']};
    color: white;
    border: none;
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{
    background-color: {c['accent_primary_hover']};
}}
QPushButton#primaryButton:pressed {{
    background-color: {c['accent_primary_dark']};
}}
QPushButton#dangerButton {{
    background-color: transparent;
    color: {c['accent_danger']};
    border: 1px solid {c['accent_danger']};
}}
QPushButton#dangerButton:hover {{
    background-color: {c['accent_danger']};
    color: white;
}}
QPushButton#successButton {{
    background-color: {c['accent_success']};
    color: white;
    border: none;
}}

/* ===== Input Fields ===== */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {c['bg_medium']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {c['border_focus']};
    background-color: {c['bg_light']};
}}
QLineEdit:disabled {{
    background-color: {c['bg_darkest']};
    color: {c['text_disabled']};
}}

/* ===== ComboBox ===== */
QComboBox {{
    background-color: {c['bg_medium']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 8px 12px;
    min-height: 20px;
}}
QComboBox:hover {{
    border-color: {c['border_hover']};
}}
QComboBox:focus {{
    border-color: {c['border_focus']};
}}
QComboBox::drop-down {{
    border: none;
    width: 30px;
}}
QComboBox QAbstractItemView {{
    background-color: {c['bg_medium']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    selection-background-color: {c['accent_primary']};
    selection-color: white;
    padding: 4px;
}}

/* ===== SpinBox ===== */
QSpinBox, QDoubleSpinBox {{
    background-color: {c['bg_medium']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 6px 12px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {c['border_focus']};
}}

/* ===== Tables ===== */
QTableWidget, QTableView {{
    background-color: {c['bg_dark']};
    alternate-background-color: {c['bg_medium']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    gridline-color: {c['border']};
    selection-background-color: {c['accent_primary']};
    selection-color: white;
}}
QHeaderView::section {{
    background-color: {c['bg_medium']};
    color: {c['text_secondary']};
    border: none;
    border-bottom: 2px solid {c['border']};
    padding: 10px 12px;
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
}}
QHeaderView::section:hover {{
    background-color: {c['bg_light']};
    color: {c['text_primary']};
}}

/* ===== Tab Widget ===== */
QTabWidget::pane {{
    border: 1px solid {c['border']};
    border-radius: 8px;
    background-color: {c['bg_dark']};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {c['bg_medium']};
    color: {c['text_secondary']};
    border: 1px solid {c['border']};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 10px 20px;
    margin-right: 2px;
}}
QTabBar::tab:hover {{
    background-color: {c['bg_light']};
    color: {c['text_primary']};
}}
QTabBar::tab:selected {{
    background-color: {c['bg_dark']};
    color: {c['accent_primary']};
    font-weight: 600;
    border-bottom: 2px solid {c['accent_primary']};
}}

/* ===== Scrollbars ===== */
QScrollBar:vertical {{
    background-color: {c['scrollbar_bg']};
    width: 10px;
    margin: 0;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background-color: {c['scrollbar_handle']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {c['scrollbar_handle_hover']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background-color: {c['scrollbar_bg']};
    height: 10px;
    margin: 0;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background-color: {c['scrollbar_handle']};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {c['scrollbar_handle_hover']};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ===== Group Box ===== */
QGroupBox {{
    background-color: {c['bg_medium']};
    border: 1px solid {c['border']};
    border-radius: 12px;
    margin-top: 16px;
    padding: 20px 16px 16px 16px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 4px 12px;
    color: {c['text_accent']};
}}

/* ===== Progress Bar ===== */
QProgressBar {{
    background-color: {c['bg_medium']};
    border: none;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    color: {c['text_primary']};
    font-size: 10px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {c['accent_primary']},
        stop:1 {c['accent_secondary']});
    border-radius: 6px;
}}

/* ===== Splitter ===== */
QSplitter::handle {{
    background-color: {c['border']};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}

/* ===== Label ===== */
QLabel#headerLabel {{
    font-size: 22px;
    font-weight: 700;
    color: {c['text_primary']};
}}
QLabel#subHeaderLabel {{
    font-size: 14px;
    color: {c['text_secondary']};
}}
QLabel#accentLabel {{
    color: {c['text_accent']};
    font-weight: 600;
}}

/* ===== Status Bar ===== */
QStatusBar {{
    background-color: {c['bg_darkest']};
    border-top: 1px solid {c['border']};
    color: {c['text_secondary']};
    font-size: 12px;
}}

/* ===== ToolTip ===== */
QToolTip {{
    background-color: {c['bg_light']};
    color: {c['text_primary']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}}

/* ===== CheckBox ===== */
QCheckBox {{
    spacing: 8px;
    color: {c['text_primary']};
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {c['border']};
    border-radius: 4px;
    background-color: {c['bg_medium']};
}}
QCheckBox::indicator:hover {{
    border-color: {c['accent_primary']};
}}
QCheckBox::indicator:checked {{
    background-color: {c['accent_primary']};
    border-color: {c['accent_primary']};
}}

/* ===== Card-style Frames ===== */
QFrame#card {{
    background-color: {c['bg_medium']};
    border: 1px solid {c['border']};
    border-radius: 12px;
    padding: 16px;
}}
QFrame#card:hover {{
    border-color: {c['border_hover']};
}}

/* ===== Dialog ===== */
QDialog {{
    background-color: {c['bg_dark']};
    border-radius: 12px;
}}
"""
