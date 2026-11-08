from datetime import date
import traceback
import secrets
import string
from pathlib import Path

import keyring
from PyQt6 import uic
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QApplication, QCalendarWidget, QCheckBox, QComboBox, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QAbstractItemView,
)

from service import ClinicService, format_phone, phone_digits

UI = Path(__file__).resolve().parent / "ui"
VAULT = "clinic_desktop_pyqt6"


def widget(form, name, kind=None):
    item = form.findChild(kind or QPushButton, name)
    if item is None:
        raise RuntimeError(f"Не найден элемент {name} в форме {form.objectName()}")
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


class ClinicApplication:
    def __init__(self):
        self.service = ClinicService()
        self.form = None
        self.user = None
        self.saved_token = None
        self.appointment_rows = []
        self.review_rows = []
        self.doctor_rows = []
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

    def login(self):
        form = self.show("login.ui")
        widget(form, "password", QLineEdit).setEchoMode(QLineEdit.EchoMode.Password)
        widget(form, "register").setText("Зарегистрироваться")

        def enter():
            try:
                user = self.service.authenticate(
                    widget(form, "login", QLineEdit).text(),
                    widget(form, "password", QLineEdit).text(),
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
        widget(form, "password", QLineEdit).returnPressed.connect(lambda: self._enter_safely(enter))

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
        password_input.setEchoMode(QLineEdit.EchoMode.Password)
        widget(form, "title", QLabel).setText("Регистрация")
        widget(form, "intro", QLabel).setText(
            "Первый аккаунт получит права регистратора" if initial
            else "Создайте аккаунт для записи на приём"
        )
        fields = ("login", "password", "name", "phone")
        inputs = {field: widget(form, field, QLineEdit) for field in fields}
        setup_phone_input(inputs["phone"])
        errors = {field: widget(form, f"{field}_error", QLabel) for field in fields}
        summary = widget(form, "form_error", QLabel)
        generated_button = widget(form, "generatePassword")
        copy_button = widget(form, "copyPassword")
        visible_checkbox = widget(form, "showPassword", QCheckBox)
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
                not inputs[field].text().strip() or (field == "phone" and not phone_digits(inputs[field].text()))
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
            visible_checkbox.setChecked(True)
            password_input.setFocus()
            password_input.selectAll()

        def copy_password():
            if password_input.text():
                QApplication.clipboard().setText(password_input.text())
                info(form, "Пароль скопирован. Сохраните его в надёжном месте.")
            else:
                show_errors({"password": "Сначала введите или сгенерируйте пароль"})

        def toggle_password(checked):
            password_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )

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
                    values["login"], values["password"], values["name"], values["phone"],
                    role="registrator" if initial else "patient",
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
        visible_checkbox.toggled.connect(toggle_password)
        connect(form, "save", create)
        connect(form, "back", self.login)

    def logout(self):
        if QMessageBox.question(self.form, "Выход", "Выйти из аккаунта на этом устройстве?") == QMessageBox.StandardButton.Yes:
            self.clear_login()
            self.user = None
            self.login()

    def change_password(self):
        old, ok = QInputDialog.getText(self.form, "Пароль", "Текущий пароль:", QLineEdit.EchoMode.Password)
        if not ok:
            return
        new, ok = QInputDialog.getText(self.form, "Пароль", "Новый пароль (не менее 8 символов):", QLineEdit.EchoMode.Password)
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

    def main(self, page=0):
        form = self.show("main.ui")
        role = self.user["role"]
        roles = {"patient": "Пациент", "doctor": "Врач", "registrator": "Регистратор"}
        widget(form, "welcome", QLabel).setText(self.user["name"])
        widget(form, "roleName", QLabel).setText(roles.get(role, role))
        table(form, "appointments", ["Дата", "Время", "Пациент", "Специальность", "Врач", "Статус", "Жалобы"])
        table(form, "reviews", ["Специалист", "Оценка", "Отзыв", "Дата", "Жалоба"])
        table(form, "doctorsTable", ["Врач", "Специальность", "Телефон", "Логин"])
        for name in ("book", "cancel"):
            widget(form, name).setVisible(role in ("patient", "registrator"))
        widget(form, "rate").setVisible(role == "patient")
        for name in ("attended", "note", "redirect", "complain"):
            widget(form, name).setVisible(role == "doctor")
        for name in ("addDoctor", "editDoctor", "removeDoctor", "addSpecialization", "moderate", "stats", "navDoctors"):
            widget(form, name).setVisible(role == "registrator")
        for name in ("navHome", "navAppointments", "navReviews", "navDoctors"):
            widget(form, name).setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        connect(form, "navHome", lambda: self.navigate(0))
        connect(form, "navAppointments", lambda: self.navigate(1))
        connect(form, "navReviews", lambda: self.navigate(2))
        connect(form, "navDoctors", lambda: self.navigate(3))
        connect(form, "homeBook", self.booking)
        widget(form, "homeBook").setVisible(role in ("patient", "registrator"))
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
        self.refresh()
        self.navigate(page)

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
            (a["date"], a["time"], a["name"], a["doctor"], a["doctor_name"], a["status"], a["complaints"])
            for a in self.appointment_rows
        ])
        self.filter_appointments(widget(self.form, "search", QLineEdit).text())
        self.review_rows = self.service.review_list(self.user)
        fill(widget(self.form, "reviews", QTableWidget), [
            (r["doctor"], "★" * r["rating"], r["comment"], r["date"], r["complaint_reason"] if r["complaint"] else "")
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
        for index, row in enumerate(self.appointment_rows):
            fields = (row["date"], row["time"], row["name"], row["doctor_name"], row["doctor"], row["status"])
            target.setRowHidden(index, term not in " ".join(fields).casefold())

    def selected(self, name, rows):
        index = widget(self.form, name, QTableWidget).currentRow()
        if index < 0 or index >= len(rows):
            alert(self.form, "Сначала выберите строку в таблице")
            return None
        return rows[index]

    def booking(self):
        form = self.show("booking.ui")
        if self.user["role"] == "patient":
            patients = [self.user]
        else:
            patients = self.service.patients()
        patient = widget(form, "patient", QComboBox)
        for item in patients:
            patient.addItem(f'{item["name"]} ({item["login"]})', item["login"])
        patient.setEnabled(self.user["role"] == "registrator")
        specs = widget(form, "specialization", QComboBox)
        specs.addItems(self.service.specializations)
        doctors = widget(form, "doctor", QComboBox)
        times = widget(form, "slot", QComboBox)
        calendar = widget(form, "calendar", QCalendarWidget)
        calendar.setMinimumDate(QDate.currentDate())
        calendar.setMaximumDate(QDate.currentDate().addDays(56))

        def update_times():
            times.clear()
            if doctors.currentData():
                times.addItems(self.service.slots(doctors.currentData(), calendar.selectedDate().toString("yyyy-MM-dd")))

        def update_doctors():
            doctors.blockSignals(True)
            doctors.clear()
            for login, user in self.service.doctors(specs.currentText()):
                doctors.addItem(user["name"], login)
            doctors.blockSignals(False)
            update_times()

        def suggest():
            recommendation = self.service.recommend(widget(form, "complaints", QTextEdit).toPlainText())
            if recommendation and specs.findText(recommendation) >= 0:
                specs.setCurrentText(recommendation)
                info(form, f"Возможная специальность: {recommendation}. Это не медицинский диагноз.")
            else:
                info(form, "Не удалось подобрать специальность. Выберите её вручную.")

        def submit():
            try:
                self.service.book(
                    patient.currentData(), doctors.currentData(),
                    calendar.selectedDate().toString("yyyy-MM-dd"), times.currentText(),
                    widget(form, "complaints", QTextEdit).toPlainText(),
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
        if record and QMessageBox.question(self.form, "Отмена", "Отменить выбранный приём?") == QMessageBox.StandardButton.Yes:
            self.action(record, "cancel")

    def attendance(self):
        record = self.selected("appointments", self.appointment_rows)
        if not record:
            return
        answer, ok = QInputDialog.getItem(self.form, "Явка", "Пациент:", ["Пришёл", "Не пришёл"], 0, False)
        if ok:
            self.action(record, "attended", answer == "Пришёл")

    def note(self):
        record = self.selected("appointments", self.appointment_rows)
        if not record:
            return
        value, ok = QInputDialog.getMultiLineText(self.form, "Заметка", "Заметка врача:", record["doctor_notes"])
        if ok:
            self.action(record, "note", value)

    def redirect(self):
        record = self.selected("appointments", self.appointment_rows)
        if not record:
            return
        doctors = [(login, user) for login, user in self.service.doctors() if login != record["doctor_login"]]
        if not doctors:
            alert(self.form, "Нет другого врача для перенаправления")
            return
        labels = [f'{user["name"]} — {user["specialization"]}' for login, user in doctors]
        selected, ok = QInputDialog.getItem(self.form, "Перенаправление", "Новый врач:", labels, 0, False)
        if not ok:
            return
        login = doctors[labels.index(selected)][0]
        free = self.service.slots(login, record["date"])
        if not free:
            alert(self.form, "Нет свободных часов на выбранную дату")
            return
        time, ok = QInputDialog.getItem(self.form, "Перенаправление", "Время:", free, 0, False)
        if ok:
            self.action(record, "redirect", (login, time))

    def action(self, record, action, value=None):
        try:
            self.service.update_appointment(self.user, record["id"], action, value)
            self.refresh()
        except ValueError as error:
            alert(self.form, error)

    def rate(self):
        record = self.selected("appointments", self.appointment_rows)
        if not record:
            return
        stars, ok = QInputDialog.getInt(self.form, "Оценка", "Количество звёзд:", 5, 1, 5)
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
        reason, ok = QInputDialog.getItem(self.form, "Жалоба", "Причина:", ["Оскорбление", "Нецензурная лексика", "Недостоверная информация", "Другое"], 0, False)
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
        answer, ok = QInputDialog.getItem(self.form, "Модерация", "Решение:", ["Оставить отзыв", "Удалить отзыв"], 0, False)
        if ok:
            try:
                self.service.update_review(self.user, review["id"], "remove" if answer == "Удалить отзыв" else "keep")
                self.refresh()
            except ValueError as error:
                alert(self.form, error)

    def doctor_form(self, login=None):
        form = self.show("doctor.ui")
        specs = widget(form, "specialization", QComboBox)
        specs.addItems(self.service.specializations)
        widget(form, "password", QLineEdit).setEchoMode(QLineEdit.EchoMode.Password)
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
            values = {field: widget(form, field, QLineEdit).text().strip() for field in ("login", "password", "name", "phone")}
            try:
                if login:
                    self.service.edit_doctor(self.user, login, values["name"], values["phone"], specs.currentText(), values["password"])
                else:
                    self.service.add_user(values["login"], values["password"], values["name"], values["phone"], "doctor", specs.currentText(), self.user)
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
        if selected and QMessageBox.question(self.form, "Удаление", f'Удалить врача {selected[1]["name"]}?') == QMessageBox.StandardButton.Yes:
            try:
                self.service.remove_doctor(self.user, selected[0])
                self.refresh()
            except ValueError as error:
                alert(self.form, error)

    def add_specialization(self):
        name, ok = QInputDialog.getText(self.form, "Специальность", "Название новой специальности:")
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
            f'Всего записей: {values["total"]}', f'Сегодня: {values["today"]}',
            f'Пациентов: {values["patients"]}', f'Врачей: {values["doctors"]}',
        ]
        lines.extend(f"{key}: {value}" for key, value in values["statuses"].items())
        info(self.form, "\n".join(lines))
