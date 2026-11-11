from datetime import date
import secrets
import string
import traceback
from pathlib import Path

import keyring
from PyQt6 import uic
from PyQt6.QtCore import QDate, QPointF, QTime, Qt
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
)

from service import ClinicService, format_phone, phone_digits

UI = Path(__file__).resolve().parent / "ui"
VAULT = "clinic_desktop_pyqt6"


def widget(form, name, kind=None):
    item = form.findChild(kind or QPushButton, name)
    if item is None:
        raise RuntimeError(f"Не найден элемент {name} в форме {form.objectName()}")
    return item


def layout(form, name, kind=None):
    item = form.findChild(kind or QHBoxLayout, name)
    if item is None:
        raise RuntimeError(f"Не найден layout {name} в форме {form.objectName()}")
    return item


def connect(form, name, callback):
    def run(*args):
        try:
            callback()
        except Exception as error:
            traceback.print_exc()
            QMessageBox.critical(None, "Ошибка", f"Не удалось выполнить действие: {error}")

    widget(form, name).clicked.connect(run)


def alert(form, message):
    QMessageBox.warning(form, "Поликлиника", str(message))


def info(form, message):
    QMessageBox.information(form, "Поликлиника", str(message))


def table(form, name, columns):
    target = widget(form, name, QTableWidget)
    target.setColumnCount(len(columns))
    target.setHorizontalHeaderLabels(columns)
    target.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    target.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    target.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    target.horizontalHeader().setStretchLastSection(True)
    target.verticalHeader().setVisible(False)
    target.setAlternatingRowColors(True)
    return target


def fill(target, rows):
    target.setRowCount(len(rows))
    for row_index, values in enumerate(rows):
        for column_index, value in enumerate(values):
            target.setItem(row_index, column_index, QTableWidgetItem(str(value)))
    target.resizeColumnsToContents()


def setup_calendar(calendar):
    """Общая настройка календарей приложения.

    Цвет выходных задаётся и для заголовков, и затем отдельно для каждой
    календарной даты через ``paint_calendar_dates``. Это важно для Qt: даты
    соседнего месяца, которые видны в текущей сетке, системная тема иногда
    рисует своим цветом и перекрывает обычный weekday format.
    """
    calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    calendar.setGridVisible(True)
    calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    calendar.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)

    weekday_format = QTextCharFormat()
    weekday_format.setForeground(QColor("#213B50"))
    weekend_format = QTextCharFormat()
    weekend_format.setForeground(QColor("#C42B38"))

    for day in (
        Qt.DayOfWeek.Monday,
        Qt.DayOfWeek.Tuesday,
        Qt.DayOfWeek.Wednesday,
        Qt.DayOfWeek.Thursday,
        Qt.DayOfWeek.Friday,
    ):
        calendar.setWeekdayTextFormat(day, weekday_format)

    calendar.setWeekdayTextFormat(Qt.DayOfWeek.Saturday, weekend_format)
    calendar.setWeekdayTextFormat(Qt.DayOfWeek.Sunday, weekend_format)


def paint_calendar_dates(calendar, work_days=None, days_off=None):
    """Явно красит каждую отображаемую выходную/нерабочую дату.

    ``setWeekdayTextFormat`` недостаточно стабилен на Windows/Fusion для дат
    соседнего месяца. Поэтому назначаем QTextCharFormat каждой дате в
    допустимом диапазоне календаря. Суббота и воскресенье всегда красные;
    если передано расписание врача, красными также становятся его прочие
    нерабочие дни и индивидуальные выходные.
    """
    normal = QColor("#213B50")
    weekend = QColor("#C42B38")
    days_off = set(days_off or [])
    work_days_set = None if work_days is None else {int(day) for day in work_days}

    minimum = calendar.minimumDate()
    maximum = calendar.maximumDate()

    # QCalendarWidget показывает в крайних строках даты соседних месяцев.
    # Часть из них может лежать уже за maximumDate(), но визуально они всё
    # равно присутствуют в сетке. Берём запас, чтобы и такие выходные были
    # красными (как 2/3 и 9/10 на переходе месяца).
    current = minimum.addDays(-14)
    paint_until = maximum.addDays(42)
    while current <= paint_until:
        iso_day = current.toString("yyyy-MM-dd")
        python_weekday = current.dayOfWeek() - 1  # Qt: 1..7; Python: 0..6
        is_weekend = current.dayOfWeek() in (6, 7)

        date_format = QTextCharFormat()
        if is_weekend:
            # Выходные всегда красные, даже если дата уже вне допустимого
            # диапазона выбора, но ещё видна в сетке соседнего месяца.
            date_format.setForeground(weekend)
        elif minimum <= current <= maximum:
            is_schedule_day_off = (
                work_days_set is not None
                and (python_weekday not in work_days_set or iso_day in days_off)
            )
            date_format.setForeground(weekend if is_schedule_day_off else normal)
        # Для будней за пределами selectable range оставляем системный
        # disabled-цвет, чтобы они не выглядели доступными.

        calendar.setDateTextFormat(current, date_format)
        current = current.addDays(1)


def setup_phone_input(field, initial=None):
    field.setPlaceholderText("+7 999 123-45-67")
    field.setMaxLength(32)
    field.setText(format_phone(initial or "+7"))

    def update(text):
        before_cursor = text[:field.cursorPosition()]
        digits_before = len(phone_digits(before_cursor))
        formatted = format_phone(text)
        if text != formatted:
            field.blockSignals(True)
            field.setText(formatted)
            field.blockSignals(False)

        if digits_before == 0:
            position = 2
        else:
            seen = 0
            position = len(formatted)
            for index, char in enumerate(formatted[2:], 2):
                if char.isdigit():
                    seen += 1
                    if seen == digits_before:
                        position = index + 1
                        break
        field.setCursorPosition(min(position, len(formatted)))

    field.textChanged.connect(update)
    field.cursorPositionChanged.connect(
        lambda old, new: field.setCursorPosition(2) if new < 2 else None
    )
    field.setCursorPosition(len(field.text()))


def _password_icon(crossed=False):
    """Monochrome eye icon drawn with Qt, so it never becomes a coloured emoji."""
    size = 22
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor("#394A59"), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    path = QPainterPath()
    path.moveTo(2.2, 11.0)
    path.cubicTo(5.1, 6.5, 8.1, 4.7, 11.0, 4.7)
    path.cubicTo(13.9, 4.7, 16.9, 6.5, 19.8, 11.0)
    path.cubicTo(16.9, 15.5, 13.9, 17.3, 11.0, 17.3)
    path.cubicTo(8.1, 17.3, 5.1, 15.5, 2.2, 11.0)
    painter.drawPath(path)
    painter.drawEllipse(QPointF(11.0, 11.0), 2.6, 2.6)

    if crossed:
        slash = QPen(QColor("#394A59"), 2.1)
        slash.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(slash)
        painter.drawLine(QPointF(3.1, 3.2), QPointF(18.9, 18.8))

    painter.end()
    return QIcon(pixmap)


def add_password_eye(field):
    """Add a native monochrome eye action inside a password QLineEdit."""
    field.setEchoMode(QLineEdit.EchoMode.Password)
    action = field.addAction(
        _password_icon(False),
        QLineEdit.ActionPosition.TrailingPosition,
    )
    action.setCheckable(True)
    action.setToolTip("Показать пароль")

    def toggle(visible):
        field.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        action.setIcon(_password_icon(visible))
        action.setToolTip("Скрыть пароль" if visible else "Показать пароль")

    action.toggled.connect(toggle)
    return action


def modal_table(columns, rows, parent=None, title=""):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(900, 520)
    root = QVBoxLayout(dialog)
    target = QTableWidget(dialog)
    target.setColumnCount(len(columns))
    target.setHorizontalHeaderLabels(columns)
    target.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    target.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    target.verticalHeader().setVisible(False)
    target.setAlternatingRowColors(True)
    fill(target, rows)
    target.horizontalHeader().setStretchLastSection(True)
    root.addWidget(target)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    if buttons.button(QDialogButtonBox.StandardButton.Close):
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    root.addWidget(buttons)
    dialog.exec()


class ClinicApplication:
    def __init__(self):
        self.service = ClinicService()
        self.form = None
        self.user = None
        self.saved_token = None
        self.appointment_rows = []
        self.review_rows = []
        self.doctor_rows = []
        self.appointment_filter_mode = "all"
        if not self.restore_login():
            self.login()

    def show(self, filename, title=None):
        form = uic.loadUi(str(UI / filename))
        if title:
            form.setWindowTitle(title)
        previous = self.form
        self.form = form
        form.show()
        if previous is not None:
            previous.hide()
            previous.deleteLater()
        return form

    def stored_token(self):
        try:
            return keyring.get_password(VAULT, "session")
        except Exception:
            return None

    def clear_login(self):
        token = self.stored_token()
        if token:
            self.service.forget(token)
        try:
            keyring.delete_password(VAULT, "session")
        except Exception:
            pass
        self.saved_token = None

    def restore_login(self):
        token = self.stored_token()
        user = self.service.restore(token)
        if not user:
            return False
        self.saved_token = token
        self.user = user
        self.main()
        return True

    # ------------------------- authentication -------------------------

    def login(self):
        form = self.show("login.ui")
        password_input = widget(form, "password", QLineEdit)
        add_password_eye(password_input)
        widget(form, "register").setText("Зарегистрироваться")

        def enter():
            try:
                user = self.service.authenticate(
                    widget(form, "login", QLineEdit).text(),
                    password_input.text(),
                )
                if widget(form, "remember", QCheckBox).isChecked():
                    token = self.service.remember(user)
                    try:
                        keyring.set_password(VAULT, "session", token)
                        self.saved_token = token
                    except Exception:
                        self.service.forget(token)
                        alert(form, "Вход выполнен, но запоминание недоступно в системе")
                else:
                    self.clear_login()
                self.user = user
                self.main()
            except ValueError as error:
                alert(form, error)

        connect(form, "enter", enter)
        connect(form, "register", self.register)
        password_input.returnPressed.connect(lambda: self._enter_safely(enter))

    def _enter_safely(self, callback):
        try:
            callback()
        except Exception as error:
            traceback.print_exc()
            QMessageBox.critical(None, "Ошибка", f"Не удалось выполнить вход: {error}")

    def register(self):
        initial = not self.service.has_users()
        form = self.show("register.ui")
        password_input = widget(form, "password", QLineEdit)
        eye = add_password_eye(password_input)
        widget(form, "title", QLabel).setText("Регистрация")
        widget(form, "intro", QLabel).setText(
            "Первый аккаунт получит права администратора" if initial
            else "Создайте аккаунт для записи на приём"
        )
        fields = ("login", "password", "name", "phone")
        inputs = {field: widget(form, field, QLineEdit) for field in fields}
        setup_phone_input(inputs["phone"])
        errors = {field: widget(form, f"{field}_error", QLabel) for field in fields}
        summary = widget(form, "form_error", QLabel)
        generated_button = widget(form, "generatePassword")
        copy_button = widget(form, "copyPassword")
        tried_submit = False

        def check_values():
            values = {field: inputs[field].text().strip() for field in fields}
            issues = {}
            if not values["login"]:
                issues["login"] = "Введите логин"
            elif len(values["login"]) > 80:
                issues["login"] = "Логин не должен превышать 80 символов"
            if not values["password"]:
                issues["password"] = "Введите пароль"
            elif len(values["password"]) < 8:
                issues["password"] = "Пароль должен содержать минимум 8 символов"
            if not values["name"]:
                issues["name"] = "Введите ФИО"
            if len(phone_digits(values["phone"])) != 10:
                issues["phone"] = "Введите 10 цифр после +7"
            return values, issues

        def show_errors(issues, message=""):
            for field, label in errors.items():
                if field in issues:
                    label.setText(issues[field])
                    label.show()
                    inputs[field].setProperty("invalid", True)
                else:
                    label.hide()
                    inputs[field].setProperty("invalid", False)
                inputs[field].style().unpolish(inputs[field])
                inputs[field].style().polish(inputs[field])
            if message:
                summary.setText(message)
            elif any(
                not inputs[field].text().strip()
                or (field == "phone" and not phone_digits(inputs[field].text()))
                for field in issues
            ):
                summary.setText("Не все обязательные поля заполнены.")
            else:
                summary.setText("Проверьте правильность заполнения полей.")
            summary.setVisible(bool(issues) or bool(message))

        def update_validation():
            if tried_submit:
                _, issues = check_values()
                show_errors(issues)

        def generate_password():
            lower = string.ascii_lowercase
            upper = string.ascii_uppercase
            digits = string.digits
            symbols = "!@#$%*-_?"
            alphabet = lower + upper + digits + symbols
            chars = [secrets.choice(group) for group in (lower, upper, digits, symbols)]
            chars.extend(secrets.choice(alphabet) for _ in range(16))
            secrets.SystemRandom().shuffle(chars)
            password_input.setText("".join(chars))
            eye.setChecked(True)
            password_input.setFocus()
            password_input.selectAll()

        def copy_password():
            if password_input.text():
                QApplication.clipboard().setText(password_input.text())
                info(form, "Пароль скопирован. Сохраните его в надёжном месте.")
            else:
                show_errors({"password": "Сначала введите или сгенерируйте пароль"})

        def create():
            nonlocal tried_submit
            tried_submit = True
            values, issues = check_values()
            show_errors(issues)
            if issues:
                inputs[next(iter(issues))].setFocus()
                return
            try:
                self.service.add_user(
                    values["login"],
                    values["password"],
                    values["name"],
                    values["phone"],
                    role="admin" if initial else "patient",
                )
            except ValueError as error:
                message = str(error)
                if "Логин уже занят" in message:
                    show_errors({"login": "Этот логин уже занят"}, "Укажите другой логин.")
                    inputs["login"].setFocus()
                else:
                    show_errors({}, message)
                return
            info(form, "Аккаунт создан. Теперь войдите в систему.")
            self.login()

        for field in fields:
            inputs[field].textChanged.connect(update_validation)
        generated_button.clicked.connect(generate_password)
        copy_button.clicked.connect(copy_password)
        connect(form, "save", create)
        connect(form, "back", self.login)

    def logout(self):
        if QMessageBox.question(
            self.form,
            "Выход",
            "Выйти из аккаунта на этом устройстве?",
        ) == QMessageBox.StandardButton.Yes:
            self.clear_login()
            self.user = None
            self.login()

    def change_password(self):
        old, ok = QInputDialog.getText(
            self.form, "Пароль", "Текущий пароль:", QLineEdit.EchoMode.Password
        )
        if not ok:
            return
        new, ok = QInputDialog.getText(
            self.form,
            "Пароль",
            "Новый пароль (не менее 8 символов):",
            QLineEdit.EchoMode.Password,
        )
        if not ok:
            return
        try:
            self.service.change_password(self.user, old, new)
            self.clear_login()
            info(self.form, "Пароль изменён. Войдите снова.")
            self.user = None
            self.login()
        except ValueError as error:
            alert(self.form, error)

    # ------------------------- main screen -------------------------

    def add_runtime_button(self, layout_name, object_name, text, callback, visible=True):
        target_layout = layout(self.form, layout_name)
        button = QPushButton(text, self.form)
        button.setObjectName(object_name)
        button.setVisible(visible)
        button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        def run():
            try:
                callback()
            except ValueError as error:
                alert(self.form, error)
            except Exception as error:
                traceback.print_exc()
                QMessageBox.critical(self.form, "Ошибка", str(error))

        button.clicked.connect(run)
        target_layout.addWidget(button)
        return button

    def make_overview_card_clickable(self, name, callback, tooltip):
        frame = widget(self.form, name, QFrame)
        frame.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        frame.setToolTip(tooltip)
        for label_item in frame.findChildren(QLabel):
            label_item.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        frame.mousePressEvent = lambda event: callback()
        frame.setStyleSheet(
            frame.styleSheet()
            + " QFrame:hover { border: 1px solid #5EA6C9; background: #F8FCFE; }"
        )

    def main(self, page=0):
        form = self.show("main.ui")
        role = self.user["role"]
        roles = {"patient": "Пациент", "doctor": "Врач", "admin": "Администратор"}
        widget(form, "welcome", QLabel).setText(self.user["name"])
        widget(form, "roleName", QLabel).setText(roles.get(role, role))
        table(
            form,
            "appointments",
            ["Дата", "Время", "Пациент", "Специальность", "Врач", "Статус", "Жалобы"],
        )
        table(form, "reviews", ["Специалист", "Оценка", "Отзыв", "Дата", "Жалоба"])
        table(form, "doctorsTable", ["Врач", "Специальность", "Телефон", "Логин"])

        for name in ("book", "cancel"):
            widget(form, name).setVisible(role in ("patient", "admin"))
        widget(form, "rate").setVisible(role == "patient")
        for name in ("attended", "note", "redirect", "complain"):
            widget(form, name).setVisible(role == "doctor")
        for name in (
            "addDoctor",
            "editDoctor",
            "removeDoctor",
            "addSpecialization",
            "moderate",
            "stats",
            "navDoctors",
        ):
            widget(form, name).setVisible(role == "admin")

        # Новые действия над записями.
        self.add_runtime_button(
            "visitActions2",
            "history",
            "История",
            self.appointment_history_dialog,
            True,
        )
        self.add_runtime_button(
            "visitActions2",
            "patientCard",
            "Карточка пациента",
            self.open_selected_patient,
            role in ("doctor", "admin"),
        )
        self.add_runtime_button(
            "visitActions2",
            "doctorCardFromVisit",
            "Карточка врача",
            self.open_selected_doctor_from_visit,
            True,
        )

        # Новые действия в разделе врачей.
        self.add_runtime_button(
            "doctorsActions",
            "doctorCard",
            "Карточка врача",
            self.open_selected_doctor,
            role == "admin",
        )
        self.add_runtime_button(
            "doctorsActions",
            "doctorSchedule",
            "Расписание",
            self.schedule_dialog,
            role == "admin",
        )

        # Быстрый доступ к собственной карточке.
        if role == "patient":
            self.add_runtime_button(
                "quickRow",
                "homeProfile",
                "Моя карточка",
                lambda: self.patient_card_dialog(self.user["login"]),
                True,
            )
        elif role == "doctor":
            self.add_runtime_button(
                "quickRow",
                "homeProfile",
                "Моя карточка",
                lambda: self.doctor_card_dialog(self.user["login"]),
                True,
            )

        for name in ("navHome", "navAppointments", "navReviews", "navDoctors"):
            widget(form, name).setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        connect(form, "navHome", lambda: self.navigate(0))
        connect(form, "navAppointments", lambda: self.open_appointments_overview("all"))
        connect(form, "navReviews", lambda: self.navigate(2))
        connect(form, "navDoctors", lambda: self.navigate(3))
        connect(form, "homeBook", self.booking)
        widget(form, "homeBook").setVisible(role in ("patient", "admin"))
        connect(form, "book", self.booking)
        connect(form, "cancel", self.cancel)
        connect(form, "rate", self.rate)
        connect(form, "refresh", self.refresh)
        connect(form, "attended", self.attendance)
        connect(form, "note", self.note)
        connect(form, "redirect", self.redirect)
        connect(form, "complain", self.complain)
        connect(form, "moderate", self.moderate)
        connect(form, "addDoctor", lambda: self.doctor_form())
        connect(form, "editDoctor", self.edit_doctor_dialog)
        connect(form, "removeDoctor", self.remove_doctor_dialog)
        connect(form, "addSpecialization", self.add_specialization)
        connect(form, "stats", self.stats)
        connect(form, "switch", self.logout)
        connect(form, "changePassword", self.change_password)
        widget(form, "search", QLineEdit).textChanged.connect(self.filter_appointments)

        # Карточки обзора теперь работают как большие кнопки.
        self.make_overview_card_clickable(
            "stat0", lambda: self.open_appointments_overview("all"), "Открыть все записи"
        )
        self.make_overview_card_clickable(
            "stat1", lambda: self.open_appointments_overview("active"), "Открыть актуальные записи"
        )
        self.make_overview_card_clickable(
            "stat2",
            lambda: self.open_appointments_overview("today"),
            "Открыть записи на сегодня",
        )
        self.make_overview_card_clickable("stat3", lambda: self.navigate(2), "Открыть отзывы")

        self.refresh()
        self.navigate(page)

    def open_appointments_overview(self, mode="all"):
        self.appointment_filter_mode = mode if mode in ("all", "active", "today") else "all"
        self.navigate(1)
        search = widget(self.form, "search", QLineEdit)
        search.clear()
        self.filter_appointments("")

    def navigate(self, page):
        widget(self.form, "pages", QStackedWidget).setCurrentIndex(page)
        for index, name in enumerate(("navHome", "navAppointments", "navReviews", "navDoctors")):
            button = widget(self.form, name)
            button.setProperty("selected", index == page)
            button.style().unpolish(button)
            button.style().polish(button)

    def refresh(self):
        self.appointment_rows = self.service.visible_appointments(self.user)
        fill(widget(self.form, "appointments", QTableWidget), [
            (
                a["date"],
                a["time"],
                a["name"],
                a["doctor"],
                a["doctor_name"],
                a.get("status_label", a["status"]),
                a["complaints"],
            )
            for a in self.appointment_rows
        ])
        self.filter_appointments(widget(self.form, "search", QLineEdit).text())
        self.review_rows = self.service.review_list(self.user)
        fill(widget(self.form, "reviews", QTableWidget), [
            (
                r["doctor"],
                "★" * r["rating"],
                r["comment"],
                r["date"],
                r["complaint_reason"] if r["complaint"] else "",
            )
            for r in self.review_rows
        ])
        self.doctor_rows = self.service.doctors()
        fill(widget(self.form, "doctorsTable", QTableWidget), [
            (doctor["name"], doctor["specialization"], doctor["phone"], login)
            for login, doctor in self.doctor_rows
        ])
        active = sum(a["status"] in ("active", "redirected") for a in self.appointment_rows)
        today = sum(a["date"] == date.today().isoformat() for a in self.appointment_rows)
        widget(self.form, "totalValue", QLabel).setText(str(len(self.appointment_rows)))
        widget(self.form, "activeValue", QLabel).setText(str(active))
        widget(self.form, "todayValue", QLabel).setText(str(today))
        widget(self.form, "reviewsValue", QLabel).setText(str(len(self.review_rows)))

    def filter_appointments(self, text):
        target = widget(self.form, "appointments", QTableWidget)
        term = text.casefold().strip()
        today = date.today().isoformat()
        for index, row in enumerate(self.appointment_rows):
            fields = (
                row["date"],
                row["time"],
                row["name"],
                row["doctor_name"],
                row["doctor"],
                row["status"],
                row.get("status_label", ""),
            )
            matches_text = term in " ".join(fields).casefold()
            if self.appointment_filter_mode == "active":
                matches_card = row["status"] in ("active", "redirected")
            elif self.appointment_filter_mode == "today":
                matches_card = row["date"] == today
            else:
                matches_card = True
            target.setRowHidden(index, not (matches_text and matches_card))

    def selected(self, name, rows):
        index = widget(self.form, name, QTableWidget).currentRow()
        if index < 0 or index >= len(rows):
            alert(self.form, "Сначала выберите строку в таблице")
            return None
        return rows[index]

    # ------------------------- booking/actions -------------------------

    def booking(self):
        form = self.show("booking.ui")
        if self.user["role"] == "patient":
            patients = [self.user]
        else:
            patients = self.service.patients()
        patient = widget(form, "patient", QComboBox)
        for item in patients:
            patient.addItem(f'{item["name"]} ({item["login"]})', item["login"])
        patient.setEnabled(self.user["role"] == "admin")
        specs = widget(form, "specialization", QComboBox)
        specs.addItems(self.service.specializations)
        doctors = widget(form, "doctor", QComboBox)
        times = widget(form, "slot", QComboBox)
        calendar = widget(form, "calendar", QCalendarWidget)
        setup_calendar(calendar)
        calendar.setMinimumDate(QDate.currentDate())
        calendar.setMaximumDate(QDate.currentDate().addDays(56))
        paint_calendar_dates(calendar)

        def update_calendar_days():
            doctor_login = doctors.currentData()
            if not doctor_login:
                paint_calendar_dates(calendar)
                return

            schedule = self.service.doctor_schedule(doctor_login)
            paint_calendar_dates(
                calendar,
                schedule.get("work_days", []),
                schedule.get("days_off", []),
            )

        def update_times():
            times.clear()
            if doctors.currentData():
                times.addItems(
                    self.service.slots(
                        doctors.currentData(),
                        calendar.selectedDate().toString("yyyy-MM-dd"),
                    )
                )

        def update_doctors():
            doctors.blockSignals(True)
            doctors.clear()
            for login, user in self.service.doctors(specs.currentText()):
                doctors.addItem(user["name"], login)
            doctors.blockSignals(False)
            update_calendar_days()
            update_times()

        def suggest():
            recommendation = self.service.recommend(
                widget(form, "complaints", QTextEdit).toPlainText()
            )
            if recommendation and specs.findText(recommendation) >= 0:
                specs.setCurrentText(recommendation)
                info(form, f"Возможная специальность: {recommendation}. Это не медицинский диагноз.")
            else:
                info(form, "Не удалось подобрать специальность. Выберите её вручную.")

        def submit():
            try:
                self.service.book(
                    patient.currentData(),
                    doctors.currentData(),
                    calendar.selectedDate().toString("yyyy-MM-dd"),
                    times.currentText(),
                    widget(form, "complaints", QTextEdit).toPlainText(),
                    actor=self.user,
                )
                info(form, "Запись создана")
                self.main(1)
            except ValueError as error:
                alert(form, error)
                update_times()

        specs.currentIndexChanged.connect(update_doctors)
        doctors.currentIndexChanged.connect(update_times)
        calendar.selectionChanged.connect(update_times)
        connect(form, "suggest", suggest)
        connect(form, "save", submit)
        connect(form, "back", lambda: self.main(1))
        update_doctors()

    def cancel(self):
        record = self.selected("appointments", self.appointment_rows)
        if record and QMessageBox.question(
            self.form, "Отмена", "Отменить выбранный приём?"
        ) == QMessageBox.StandardButton.Yes:
            self.action(record, "cancel")

    def attendance(self):
        record = self.selected("appointments", self.appointment_rows)
        if not record:
            return
        answer, ok = QInputDialog.getItem(
            self.form, "Явка", "Пациент:", ["Пришёл", "Не пришёл"], 0, False
        )
        if ok:
            self.action(record, "attended", answer == "Пришёл")

    def note(self):
        record = self.selected("appointments", self.appointment_rows)
        if not record:
            return
        value, ok = QInputDialog.getMultiLineText(
            self.form, "Заметка", "Заметка врача:", record["doctor_notes"]
        )
        if ok:
            self.action(record, "note", value)

    def redirect(self):
        record = self.selected("appointments", self.appointment_rows)
        if not record:
            return
        doctors = [
            (login, user)
            for login, user in self.service.doctors()
            if login != record["doctor_login"]
        ]
        if not doctors:
            alert(self.form, "Нет другого врача для перенаправления")
            return
        labels = [f'{user["name"]} — {user["specialization"]}' for login, user in doctors]
        selected, ok = QInputDialog.getItem(
            self.form, "Перенаправление", "Новый врач:", labels, 0, False
        )
        if not ok:
            return
        login = doctors[labels.index(selected)][0]
        free = self.service.slots(login, record["date"])
        if not free:
            alert(self.form, "Нет свободных часов на выбранную дату")
            return
        time, ok = QInputDialog.getItem(
            self.form, "Перенаправление", "Время:", free, 0, False
        )
        if ok:
            self.action(record, "redirect", (login, time))

    def action(self, record, action, value=None):
        try:
            self.service.update_appointment(self.user, record["id"], action, value)
            self.refresh()
        except ValueError as error:
            alert(self.form, error)

    def appointment_history_dialog(self):
        record = self.selected("appointments", self.appointment_rows)
        if not record:
            return
        rows = self.service.appointment_history(self.user, record["id"])
        modal_table(
            ["Дата и время", "Кто", "Событие"],
            [(row["time"], row["actor"], row["message"]) for row in rows],
            self.form,
            f'История приёма — {record["name"]}',
        )

    # ------------------------- patient / doctor cards -------------------------

    def open_selected_patient(self):
        record = self.selected("appointments", self.appointment_rows)
        if record:
            self.patient_card_dialog(record["patient_login"])

    def patient_card_dialog(self, patient_login):
        profile = self.service.patient_profile(self.user, patient_login)
        dialog = QDialog(self.form)
        dialog.setWindowTitle(f'Карточка пациента — {profile["name"]}')
        dialog.resize(940, 600)
        root = QVBoxLayout(dialog)

        header = QLabel(
            f'<b style="font-size:18px">{profile["name"]}</b><br>'
            f'Логин: {profile["login"]} &nbsp;&nbsp; Телефон: {format_phone(profile["phone"])}<br>'
            f'Всего записей: {profile["total_visits"]} &nbsp; '
            f'Завершено: {profile["completed"]} &nbsp; '
            f'Неявки: {profile["no_show"]} &nbsp; '
            f'Отменено: {profile["cancelled"]}'
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(header)

        target = QTableWidget(dialog)
        target.setColumnCount(7)
        target.setHorizontalHeaderLabels(
            ["Дата", "Время", "Врач", "Специальность", "Статус", "Жалобы", "Заметка врача"]
        )
        target.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        target.verticalHeader().setVisible(False)
        target.setAlternatingRowColors(True)
        fill(target, [
            (
                row["date"],
                row["time"],
                row["doctor_name"],
                row["doctor"],
                row.get("status_label", row["status"]),
                row["complaints"],
                row["doctor_notes"],
            )
            for row in profile["appointments"]
        ])
        target.horizontalHeader().setStretchLastSection(True)
        root.addWidget(target)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        if close.button(QDialogButtonBox.StandardButton.Close):
            close.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        close.rejected.connect(dialog.reject)
        root.addWidget(close)
        dialog.exec()

    def open_selected_doctor_from_visit(self):
        record = self.selected("appointments", self.appointment_rows)
        if record and record.get("doctor_login"):
            self.doctor_card_dialog(record["doctor_login"])

    def open_selected_doctor(self):
        selected = self.selected("doctorsTable", self.doctor_rows)
        if selected:
            self.doctor_card_dialog(selected[0])

    def doctor_card_dialog(self, doctor_login):
        profile = self.service.doctor_profile(self.user, doctor_login)
        dialog = QDialog(self.form)
        dialog.setWindowTitle(f'Карточка врача — {profile["name"]}')
        dialog.resize(940, 600)
        root = QVBoxLayout(dialog)
        rating = f'{profile["rating"]}/5 ({profile["review_count"]} отзывов)' if profile["review_count"] else "нет оценок"
        header = QLabel(
            f'<b style="font-size:18px">{profile["name"]}</b><br>'
            f'{profile["specialization"]} &nbsp;&nbsp; Телефон: {format_phone(profile["phone"])}<br>'
            f'Расписание: {profile["schedule_text"]}<br>'
            f'Записей всего: {profile["total_appointments"]} &nbsp; '
            f'Предстоящих: {profile["upcoming"]} &nbsp; '
            f'Завершено: {profile["completed"]} &nbsp; Рейтинг: {rating}'
        )
        header.setWordWrap(True)
        root.addWidget(header)

        if profile["appointments"]:
            target = QTableWidget(dialog)
            target.setColumnCount(6)
            target.setHorizontalHeaderLabels(
                ["Дата", "Время", "Пациент", "Статус", "Жалобы", "Заметка"]
            )
            target.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            target.verticalHeader().setVisible(False)
            target.setAlternatingRowColors(True)
            fill(target, [
                (
                    row["date"],
                    row["time"],
                    row["name"],
                    row.get("status_label", row["status"]),
                    row["complaints"],
                    row["doctor_notes"],
                )
                for row in profile["appointments"]
            ])
            target.horizontalHeader().setStretchLastSection(True)
            root.addWidget(target)
        else:
            hint = QLabel("Подробный список пациентов доступен только самому врачу и администратору.")
            hint.setStyleSheet("color:#70879B;")
            root.addWidget(hint)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        if close.button(QDialogButtonBox.StandardButton.Close):
            close.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        close.rejected.connect(dialog.reject)
        root.addWidget(close)
        dialog.exec()

    # ------------------------- schedule editor -------------------------

    def schedule_dialog(self):
        selected = self.selected("doctorsTable", self.doctor_rows)
        if not selected:
            return
        doctor_login, doctor = selected
        current = self.service.doctor_schedule(doctor_login)

        dialog = QDialog(self.form)
        dialog.setWindowTitle(f'Расписание — {doctor["name"]}')
        dialog.resize(620, 650)
        root = QVBoxLayout(dialog)

        days_box = QGroupBox("Рабочие дни")
        days_layout = QHBoxLayout(days_box)
        day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        day_checks = []
        for index, day_name in enumerate(day_names):
            check = QCheckBox(day_name)
            check.setChecked(index in current["work_days"])
            days_layout.addWidget(check)
            day_checks.append(check)
        root.addWidget(days_box)

        form_layout = QFormLayout()
        start = QTimeEdit(QTime.fromString(current["start_time"], "HH:mm"))
        start.setDisplayFormat("HH:mm")
        end = QTimeEdit(QTime.fromString(current["end_time"], "HH:mm"))
        end.setDisplayFormat("HH:mm")
        duration = QComboBox()
        for minutes in (15, 20, 30, 45, 60, 90, 120):
            duration.addItem(f"{minutes} мин", minutes)
        duration_index = duration.findData(current["slot_minutes"])
        if duration_index >= 0:
            duration.setCurrentIndex(duration_index)
        break_start = QTimeEdit(QTime.fromString(current["break_start"] or "13:00", "HH:mm"))
        break_start.setDisplayFormat("HH:mm")
        break_end = QTimeEdit(QTime.fromString(current["break_end"] or "14:00", "HH:mm"))
        break_end.setDisplayFormat("HH:mm")
        use_break = QCheckBox("Использовать перерыв")
        use_break.setChecked(bool(current["break_start"] and current["break_end"]))
        break_start.setEnabled(use_break.isChecked())
        break_end.setEnabled(use_break.isChecked())
        use_break.toggled.connect(break_start.setEnabled)
        use_break.toggled.connect(break_end.setEnabled)

        form_layout.addRow("Начало работы:", start)
        form_layout.addRow("Последняя запись:", end)
        form_layout.addRow("Длительность приёма:", duration)
        form_layout.addRow("", use_break)
        form_layout.addRow("Начало перерыва:", break_start)
        form_layout.addRow("Конец перерыва:", break_end)
        root.addLayout(form_layout)

        off_box = QGroupBox("Выходные / отпуск")
        off_layout = QVBoxLayout(off_box)
        calendar = QCalendarWidget()
        setup_calendar(calendar)
        calendar.setMinimumDate(QDate.currentDate())
        calendar.setMaximumDate(QDate.currentDate().addYears(2))
        paint_calendar_dates(calendar)
        off_layout.addWidget(calendar)
        actions = QHBoxLayout()
        add_day = QPushButton("+ Добавить дату")
        remove_day = QPushButton("Убрать выбранную")
        actions.addWidget(add_day)
        actions.addWidget(remove_day)
        off_layout.addLayout(actions)
        days_list = QListWidget()
        days_list.addItems(current["days_off"])
        off_layout.addWidget(days_list)
        root.addWidget(off_box)

        def add_off():
            day = calendar.selectedDate().toString("yyyy-MM-dd")
            try:
                self.service.add_doctor_day_off(self.user, doctor_login, day)
                if not days_list.findItems(day, Qt.MatchFlag.MatchExactly):
                    days_list.addItem(day)
            except ValueError as error:
                alert(dialog, error)

        def remove_off():
            item = days_list.currentItem()
            if item:
                self.service.remove_doctor_day_off(self.user, doctor_login, item.text())
                days_list.takeItem(days_list.row(item))

        add_day.clicked.connect(add_off)
        remove_day.clicked.connect(remove_off)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        if buttons.button(QDialogButtonBox.StandardButton.Save):
            buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        if buttons.button(QDialogButtonBox.StandardButton.Cancel):
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        root.addWidget(buttons)

        def save():
            try:
                self.service.save_doctor_schedule(
                    self.user,
                    doctor_login,
                    [index for index, check in enumerate(day_checks) if check.isChecked()],
                    start.time().toString("HH:mm"),
                    end.time().toString("HH:mm"),
                    duration.currentData(),
                    break_start.time().toString("HH:mm") if use_break.isChecked() else "",
                    break_end.time().toString("HH:mm") if use_break.isChecked() else "",
                )
                dialog.accept()
                self.refresh()
            except ValueError as error:
                alert(dialog, error)

        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()

    # ------------------------- reviews -------------------------

    def rate(self):
        record = self.selected("appointments", self.appointment_rows)
        if not record:
            return
        stars, ok = QInputDialog.getInt(
            self.form, "Оценка", "Количество звёзд:", 5, 1, 5
        )
        if not ok:
            return
        comment, ok = QInputDialog.getMultiLineText(self.form, "Отзыв", "Комментарий:")
        if ok:
            try:
                self.service.rate(self.user, record["id"], stars, comment)
                self.refresh()
                self.navigate(2)
            except ValueError as error:
                alert(self.form, error)

    def complain(self):
        review = self.selected("reviews", self.review_rows)
        if not review:
            return
        reason, ok = QInputDialog.getItem(
            self.form,
            "Жалоба",
            "Причина:",
            ["Оскорбление", "Нецензурная лексика", "Недостоверная информация", "Другое"],
            0,
            False,
        )
        if ok:
            try:
                self.service.update_review(self.user, review["id"], "complain", reason)
                self.refresh()
            except ValueError as error:
                alert(self.form, error)

    def moderate(self):
        review = self.selected("reviews", self.review_rows)
        if not review:
            return
        answer, ok = QInputDialog.getItem(
            self.form,
            "Модерация",
            "Решение:",
            ["Оставить отзыв", "Удалить отзыв"],
            0,
            False,
        )
        if ok:
            try:
                self.service.update_review(
                    self.user,
                    review["id"],
                    "remove" if answer == "Удалить отзыв" else "keep",
                )
                self.refresh()
            except ValueError as error:
                alert(self.form, error)

    # ------------------------- doctor management -------------------------

    def doctor_form(self, login=None):
        form = self.show("doctor.ui")
        specs = widget(form, "specialization", QComboBox)
        specs.addItems(self.service.specializations)
        password_input = widget(form, "password", QLineEdit)
        add_password_eye(password_input)
        setup_phone_input(widget(form, "phone", QLineEdit))
        if login:
            record = self.service.get_doctor(login)
            if not record:
                alert(form, "Врач не найден")
                self.main(3)
                return
            widget(form, "title", QLabel).setText("Редактирование врача")
            widget(form, "login", QLineEdit).setText(login)
            widget(form, "login", QLineEdit).setReadOnly(True)
            for field in ("name", "phone"):
                widget(form, field, QLineEdit).setText(record[field])
            specs.setCurrentText(record["specialization"])

        def submit():
            values = {
                field: widget(form, field, QLineEdit).text().strip()
                for field in ("login", "password", "name", "phone")
            }
            try:
                if login:
                    self.service.edit_doctor(
                        self.user,
                        login,
                        values["name"],
                        values["phone"],
                        specs.currentText(),
                        values["password"],
                    )
                else:
                    self.service.add_user(
                        values["login"],
                        values["password"],
                        values["name"],
                        values["phone"],
                        "doctor",
                        specs.currentText(),
                        self.user,
                    )
                self.main(3)
            except ValueError as error:
                alert(form, error)

        connect(form, "save", submit)
        connect(form, "back", lambda: self.main(3))

    def edit_doctor_dialog(self):
        selected = self.selected("doctorsTable", self.doctor_rows)
        if selected:
            self.doctor_form(selected[0])

    def remove_doctor_dialog(self):
        selected = self.selected("doctorsTable", self.doctor_rows)
        if selected and QMessageBox.question(
            self.form,
            "Удаление",
            f'Удалить врача {selected[1]["name"]}?',
        ) == QMessageBox.StandardButton.Yes:
            try:
                self.service.remove_doctor(self.user, selected[0])
                self.refresh()
            except ValueError as error:
                alert(self.form, error)

    def add_specialization(self):
        name, ok = QInputDialog.getText(
            self.form, "Специальность", "Название новой специальности:"
        )
        if ok:
            try:
                self.service.add_specialization(name, self.user)
                info(self.form, "Специальность добавлена")
            except ValueError as error:
                alert(self.form, error)

    def stats(self):
        try:
            values = self.service.stats(self.user)
        except ValueError as error:
            alert(self.form, error)
            return
        lines = [
            f'Всего записей: {values["total"]}',
            f'Сегодня: {values["today"]}',
            f'Пациентов: {values["patients"]}',
            f'Врачей: {values["doctors"]}',
        ]
        lines.extend(f"{key}: {value}" for key, value in values["statuses"].items())
        info(self.form, "\n".join(lines))
