import hashlib
import hmac
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from database import (
    Appointment,
    AppointmentEvent,
    Database,
    DoctorDayOff,
    DoctorSchedule,
    LoginSession,
    Review,
    Specialization,
    User,
)

SPECIALIZATIONS = ["Терапевт", "Хирург", "Окулист", "ЛОР", "Кардиолог", "Невролог", "Дерматолог"]
SYMPTOMS = {
    "Терапевт": ["температур", "жар", "озноб", "слабост", "кашел", "кашель", "просту", "грипп", "давлен"],
    "ЛОР": ["нос", "горл", "ух", "отит", "гайморит", "насморк", "ангин"],
    "Окулист": ["глаз", "зрен", "близорук", "линз", "конъюнктивит"],
    "Хирург": ["перелом", "ушиб", "ран", "вывих", "растяжен", "ожог"],
    "Кардиолог": ["сердц", "аритм", "тахикард", "груд", "одышк", "пульс"],
    "Невролог": ["головокружен", "мигрен", "спин", "поясниц", "онемен", "судорог"],
    "Дерматолог": ["сып", "прыщ", "зуд", "экзем", "псориаз", "кож"],
}

STATUS_LABELS = {
    "active": "Активна",
    "redirected": "Перенаправлена",
    "completed": "Завершена",
    "cancelled": "Отменена",
    "no_show": "Неявка",
}

DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def phone_digits(value):
    text = str(value or "").strip()
    digits = "".join(char for char in text if char in "0123456789")
    if text.startswith("+7") and digits.startswith("7"):
        return digits[1:]
    if len(digits) == 11 and digits[0] in "78":
        return digits[1:]
    if digits == "7" and text.startswith("+7"):
        return ""
    return digits


def format_phone(value):
    digits = phone_digits(value)[:10]
    result = "+7"
    if digits:
        result += " " + digits[:3]
    if len(digits) > 3:
        result += " " + digits[3:6]
    if len(digits) > 6:
        result += "-" + digits[6:8]
    if len(digits) > 8:
        result += "-" + digits[8:10]
    return result


def normalize_phone(value, required=True):
    digits = phone_digits(value)
    if not digits:
        if required:
            raise ValueError("Введите 10 цифр после +7")
        return ""
    if len(digits) != 10:
        raise ValueError("Введите 10 цифр после +7")
    return "+7" + digits


def password_hash(password):
    salt = secrets.token_bytes(16)
    result = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${result.hex()}"


def check_password(password, saved):
    if saved.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, stored = saved.split("$", 3)
            calculated = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
            )
            return hmac.compare_digest(calculated, bytes.fromhex(stored))
        except (ValueError, TypeError):
            return False
    if saved.startswith("pbkdf2:"):
        try:
            _, salt, digest = saved.split(":", 2)
            candidate = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000
            )
            return hmac.compare_digest(candidate, bytes.fromhex(digest))
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(hashlib.sha256(password.encode("utf-8")).hexdigest(), saved)


def user_dict(user):
    return {
        "login": user.login,
        "name": user.name,
        "phone": user.phone,
        "role": "admin" if user.role == "registrator" else user.role,
        "specialization": user.specialization,
    }


def appointment_dict(a):
    return {
        "id": a.id,
        "patient_login": a.patient_login,
        "name": a.name,
        "phone": a.phone,
        "doctor_login": a.doctor_login,
        "doctor_name": a.doctor_name,
        "doctor": a.doctor,
        "date": a.day,
        "time": a.time,
        "complaints": a.complaints,
        "status": a.status,
        "status_label": STATUS_LABELS.get(a.status, a.status),
        "doctor_notes": a.doctor_notes,
        "attended": a.attended,
        "rated": a.rated,
    }


def review_dict(r):
    return {
        "id": r.id,
        "doctor": r.doctor,
        "doctor_login": r.doctor_login,
        "rating": r.rating,
        "comment": r.comment,
        "date": r.day,
        "complaint": r.complaint,
        "complaint_reason": r.complaint_reason,
        "removed": r.removed,
    }


def schedule_dict(schedule, days_off=None):
    work_days = [int(value) for value in str(schedule.work_days or "").split(",") if value != ""]
    return {
        "doctor_login": schedule.doctor_login,
        "work_days": work_days,
        "start_time": schedule.start_time,
        "end_time": schedule.end_time,
        "slot_minutes": schedule.slot_minutes,
        "break_start": schedule.break_start,
        "break_end": schedule.break_end,
        "days_off": list(days_off or []),
    }


class ClinicService:
    def __init__(self, folder=None):
        self.db = Database(folder)
        self._init_specializations()
        self._import_legacy()

    def _init_specializations(self):
        with self.db.clinic.begin() as session:
            if session.scalar(select(func.count()).select_from(Specialization)) == 0:
                session.add_all(Specialization(name=name) for name in SPECIALIZATIONS)

    def _import_legacy(self):
        folder = self.db.accounts_engine.url.database
        path = Path(folder).parent
        users_path = path / "users.json"
        appointments_path = path / "appointments.json"
        reviews_path = path / "reviews.json"
        spec_path = path / "specializations.json"
        with self.db.accounts() as session:
            existing = session.scalar(select(func.count()).select_from(User))
        if existing or not users_path.exists():
            return
        try:
            users = json.loads(users_path.read_text(encoding="utf-8"))
            appointments = json.loads(appointments_path.read_text(encoding="utf-8")) if appointments_path.exists() else []
            reviews = json.loads(reviews_path.read_text(encoding="utf-8")) if reviews_path.exists() else []
            specs = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.exists() else []
            if isinstance(reviews, dict):
                reviews = reviews.get("reviews", [])
            if not isinstance(users, dict) or not isinstance(appointments, list) or not isinstance(reviews, list):
                return
        except (OSError, ValueError, TypeError):
            return

        with self.db.accounts.begin() as session:
            for login, data in users.items():
                if isinstance(data, dict) and data.get("password_hash"):
                    role = data.get("role", "patient")
                    if role == "registrator":
                        role = "admin"
                    session.add(User(
                        login=login,
                        password_hash=data["password_hash"],
                        name=data.get("name", login),
                        phone=data.get("phone", ""),
                        role=role,
                        specialization=data.get("specialization", ""),
                        phone_confirmed=bool(data.get("phone_confirmed", False)),
                    ))

        with self.db.clinic.begin() as session:
            for spec in specs if isinstance(specs, list) else []:
                if isinstance(spec, str) and spec.strip():
                    session.merge(Specialization(name=spec.strip()))
            for row in appointments:
                if not isinstance(row, dict) or not row.get("date") or not row.get("time"):
                    continue
                session.add(Appointment(
                    id=row.get("id") or secrets.token_hex(12),
                    patient_login=row.get("patient_login", ""),
                    name=row.get("name", ""),
                    phone=row.get("phone", ""),
                    doctor_login=row.get("doctor_login") or None,
                    doctor_name=row.get("doctor_name", ""),
                    doctor=row.get("doctor", ""),
                    day=row["date"],
                    time=row["time"],
                    complaints=row.get("complaints", ""),
                    status=row.get("status", "active"),
                    doctor_notes=row.get("doctor_notes", ""),
                    attended=row.get("attended"),
                    rated=bool(row.get("rated", False)),
                ))
            for row in reviews:
                if not isinstance(row, dict):
                    continue
                session.add(Review(
                    id=row.get("id") or secrets.token_hex(12),
                    doctor=row.get("doctor", ""),
                    doctor_login=row.get("doctor_login") or None,
                    rating=int(row.get("rating", 5)),
                    comment=row.get("comment", ""),
                    day=row.get("date", date.today().isoformat()),
                    complaint=bool(row.get("complaint", False)),
                    complaint_reason=row.get("complaint_reason", ""),
                    removed=bool(row.get("removed", False)),
                ))

    def has_users(self):
        with self.db.accounts() as session:
            return session.scalar(select(func.count()).select_from(User)) > 0

    def add_user(self, login, password, name, phone, role="patient", specialization="", actor=None):
        login = login.strip()
        name = name.strip()
        if not login or len(login) > 80 or not name:
            raise ValueError("Введите логин и ФИО")
        if len(password) < 8:
            raise ValueError("Пароль должен быть не короче 8 символов")
        if role == "registrator":
            role = "admin"
        if role not in ("patient", "doctor", "admin"):
            raise ValueError("Неизвестная роль")
        if role != "patient":
            first_admin = role == "admin" and not self.has_users()
            actor_is_admin = actor and actor.get("role") in ("admin", "registrator")
            if not first_admin and not actor_is_admin:
                raise ValueError("Создавать сотрудников может только администратор")
        phone = normalize_phone(phone, required=role == "patient")
        with self.db.accounts.begin() as session:
            if session.get(User, login):
                raise ValueError("Логин уже занят")
            session.add(User(
                login=login,
                password_hash=password_hash(password),
                name=name,
                phone=phone,
                role=role,
                specialization=specialization if role == "doctor" else "",
            ))
        if role == "doctor":
            self._ensure_schedule(login)
        return login

    def authenticate(self, login, password):
        with self.db.accounts.begin() as session:
            user = session.get(User, login.strip())
            if not user or not check_password(password, user.password_hash):
                raise ValueError("Неверный логин или пароль")
            if user.role == "registrator":
                user.role = "admin"
            if not user.password_hash.startswith("pbkdf2_sha256$"):
                user.password_hash = password_hash(password)
            return user_dict(user)

    def remember(self, user):
        token = secrets.token_urlsafe(48)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
        with self.db.accounts.begin() as session:
            session.add(LoginSession(token_hash=digest, user_login=user["login"], expires_at=expiry))
        return token

    def restore(self, token):
        if not token:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.db.accounts.begin() as session:
            saved = session.get(LoginSession, digest)
            if not saved:
                return None
            if saved.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None):
                session.delete(saved)
                return None
            user = session.get(User, saved.user_login)
            return user_dict(user) if user else None

    def forget(self, token):
        if token:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            with self.db.accounts.begin() as session:
                saved = session.get(LoginSession, digest)
                if saved:
                    session.delete(saved)

    def change_password(self, user, old_password, new_password):
        if len(new_password) < 8:
            raise ValueError("Новый пароль должен быть не короче 8 символов")
        with self.db.accounts.begin() as session:
            record = session.get(User, user["login"])
            if not record or not check_password(old_password, record.password_hash):
                raise ValueError("Текущий пароль неверен")
            record.password_hash = password_hash(new_password)
            for active in session.scalars(select(LoginSession).where(LoginSession.user_login == record.login)):
                session.delete(active)

    @property
    def specializations(self):
        with self.db.clinic() as session:
            return list(session.scalars(select(Specialization.name).order_by(Specialization.name)))

    def add_specialization(self, name, actor):
        if actor.get("role") not in ("admin", "registrator"):
            raise ValueError("Недостаточно прав")
        name = name.strip()
        if not name:
            raise ValueError("Укажите специальность")
        with self.db.clinic.begin() as session:
            if session.get(Specialization, name):
                raise ValueError("Такая специальность уже существует")
            session.add(Specialization(name=name))

    def patients(self):
        with self.db.accounts() as session:
            return [user_dict(u) for u in session.scalars(select(User).where(User.role == "patient").order_by(User.name))]

    def doctors(self, specialization=""):
        with self.db.accounts() as session:
            query = select(User).where(User.role == "doctor")
            if specialization:
                query = query.where(User.specialization == specialization)
            return [(u.login, user_dict(u)) for u in session.scalars(query.order_by(User.name))]

    def get_doctor(self, login):
        with self.db.accounts() as session:
            u = session.get(User, login)
            return user_dict(u) if u and u.role == "doctor" else None

    def recommend(self, text):
        text = text.lower().strip()
        if len(text) < 3:
            return ""
        scores = {spec: sum(word in text for word in words) for spec, words in SYMPTOMS.items()}
        return max(scores, key=scores.get) if max(scores.values(), default=0) > 0 else ""

    # ------------------------- schedules -------------------------

    def _ensure_schedule(self, doctor_login):
        with self.db.clinic.begin() as session:
            schedule = session.get(DoctorSchedule, doctor_login)
            if not schedule:
                session.add(DoctorSchedule(doctor_login=doctor_login))

    def doctor_schedule(self, doctor_login):
        if not self.get_doctor(doctor_login):
            raise ValueError("Врач не найден")
        self._ensure_schedule(doctor_login)
        with self.db.clinic() as session:
            schedule = session.get(DoctorSchedule, doctor_login)
            days_off = list(session.scalars(
                select(DoctorDayOff.day)
                .where(DoctorDayOff.doctor_login == doctor_login)
                .order_by(DoctorDayOff.day)
            ))
            return schedule_dict(schedule, days_off)

    def save_doctor_schedule(
        self,
        actor,
        doctor_login,
        work_days,
        start_time,
        end_time,
        slot_minutes,
        break_start="",
        break_end="",
    ):
        if actor.get("role") not in ("admin", "registrator"):
            raise ValueError("Недостаточно прав")
        if not self.get_doctor(doctor_login):
            raise ValueError("Врач не найден")
        work_days = sorted({int(day) for day in work_days if 0 <= int(day) <= 6})
        if not work_days:
            raise ValueError("Выберите хотя бы один рабочий день")
        try:
            start = datetime.strptime(start_time, "%H:%M")
            end = datetime.strptime(end_time, "%H:%M")
        except ValueError:
            raise ValueError("Некорректное время") from None
        if end <= start:
            raise ValueError("Последняя запись должна быть позже начала работы")
        slot_minutes = int(slot_minutes)
        if slot_minutes not in (15, 20, 30, 45, 60, 90, 120):
            raise ValueError("Недопустимая длительность приёма")
        if bool(break_start) != bool(break_end):
            raise ValueError("Укажите начало и конец перерыва")
        if break_start and break_end:
            try:
                b_start = datetime.strptime(break_start, "%H:%M")
                b_end = datetime.strptime(break_end, "%H:%M")
            except ValueError:
                raise ValueError("Некорректное время перерыва") from None
            if b_end <= b_start or b_start < start or b_end > end + timedelta(minutes=slot_minutes):
                raise ValueError("Перерыв должен находиться внутри рабочего времени")
        with self.db.clinic.begin() as session:
            schedule = session.get(DoctorSchedule, doctor_login)
            if not schedule:
                schedule = DoctorSchedule(doctor_login=doctor_login)
                session.add(schedule)
            schedule.work_days = ",".join(str(day) for day in work_days)
            schedule.start_time = start_time
            schedule.end_time = end_time
            schedule.slot_minutes = slot_minutes
            schedule.break_start = break_start
            schedule.break_end = break_end

    def add_doctor_day_off(self, actor, doctor_login, day):
        if actor.get("role") not in ("admin", "registrator"):
            raise ValueError("Недостаточно прав")
        try:
            selected = date.fromisoformat(day)
        except ValueError:
            raise ValueError("Некорректная дата") from None
        if selected < date.today():
            raise ValueError("Нельзя добавить прошедшую дату")
        with self.db.clinic.begin() as session:
            if not session.get(DoctorDayOff, {"doctor_login": doctor_login, "day": day}):
                session.add(DoctorDayOff(doctor_login=doctor_login, day=day))

    def remove_doctor_day_off(self, actor, doctor_login, day):
        if actor.get("role") not in ("admin", "registrator"):
            raise ValueError("Недостаточно прав")
        with self.db.clinic.begin() as session:
            record = session.get(DoctorDayOff, {"doctor_login": doctor_login, "day": day})
            if record:
                session.delete(record)

    def schedule_summary(self, schedule):
        days = ", ".join(DAY_NAMES[i] for i in schedule["work_days"])
        text = f'{days}; {schedule["start_time"]}–{schedule["end_time"]}; {schedule["slot_minutes"]} мин.'
        if schedule.get("break_start") and schedule.get("break_end"):
            text += f'; перерыв {schedule["break_start"]}–{schedule["break_end"]}'
        return text

    def _generated_slots(self, schedule):
        start = datetime.strptime(schedule["start_time"], "%H:%M")
        end = datetime.strptime(schedule["end_time"], "%H:%M")
        step = timedelta(minutes=schedule["slot_minutes"])
        break_start = datetime.strptime(schedule["break_start"], "%H:%M") if schedule.get("break_start") else None
        break_end = datetime.strptime(schedule["break_end"], "%H:%M") if schedule.get("break_end") else None
        result = []
        current = start
        while current <= end:
            if not (break_start and break_end and break_start <= current < break_end):
                result.append(current.strftime("%H:%M"))
            current += step
        return result

    def slots(self, doctor_login, day):
        doctor = self.get_doctor(doctor_login)
        if not doctor:
            return []
        try:
            selected = date.fromisoformat(day)
        except ValueError:
            return []
        if selected < date.today() or selected > date.today() + timedelta(days=56):
            return []
        schedule = self.doctor_schedule(doctor_login)
        if selected.weekday() not in schedule["work_days"] or day in schedule["days_off"]:
            return []
        with self.db.clinic() as session:
            busy_query = select(Appointment.time).where(
                Appointment.day == day,
                Appointment.status.not_in(["cancelled", "no_show"]),
                or_(
                    Appointment.doctor_login == doctor_login,
                    and_(Appointment.doctor_login.is_(None), Appointment.doctor == doctor["specialization"]),
                ),
            )
            occupied = set(session.scalars(busy_query))
        return [
            value for value in self._generated_slots(schedule)
            if value not in occupied and datetime.fromisoformat(f"{day}T{value}") > datetime.now()
        ]

    # ------------------------- appointments -------------------------

    def _add_event(self, session, appointment_id, event_type, message, actor=None):
        session.add(AppointmentEvent(
            id=secrets.token_hex(12),
            appointment_id=appointment_id,
            event_type=event_type,
            message=str(message)[:5000],
            actor_login=(actor or {}).get("login", ""),
            actor_name=(actor or {}).get("name", "Система") or "Система",
        ))

    def book(self, patient_login, doctor_login, day, time, complaints="", actor=None):
        with self.db.accounts() as accounts:
            patient = accounts.get(User, patient_login)
            doctor = accounts.get(User, doctor_login) if doctor_login else None
            if not patient or patient.role != "patient":
                raise ValueError("Выберите пациента")
            if not doctor or doctor.role != "doctor":
                raise ValueError("Выберите врача")
            patient_info = user_dict(patient)
            doctor_info = user_dict(doctor)
        if time not in self.slots(doctor_login, day):
            raise ValueError("Выбранное время занято или недоступно")
        record = Appointment(
            id=secrets.token_hex(12),
            patient_login=patient_login,
            name=patient_info["name"],
            phone=patient_info["phone"],
            doctor_login=doctor_login,
            doctor=doctor_info["specialization"],
            doctor_name=doctor_info["name"],
            day=day,
            time=time,
            complaints=complaints.strip()[:5000],
            status="active",
        )
        try:
            with self.db.clinic.begin() as session:
                session.add(record)
                session.flush()
                creator = actor or patient_info
                self._add_event(
                    session,
                    record.id,
                    "created",
                    f'Запись создана на {day} в {time} к врачу {doctor_info["name"]} ({doctor_info["specialization"]}).',
                    creator,
                )
        except IntegrityError:
            raise ValueError("Это время только что заняли. Выберите другое") from None
        return appointment_dict(record)

    def _visible_filter(self, user):
        role = user["role"]
        if role in ("admin", "registrator"):
            return True
        if role == "patient":
            return Appointment.patient_login == user["login"]
        if role == "doctor":
            return or_(
                Appointment.doctor_login == user["login"],
                and_(Appointment.doctor_login.is_(None), Appointment.doctor == user.get("specialization")),
            )
        return False

    def visible_appointments(self, user):
        with self.db.clinic() as session:
            query = select(Appointment).where(self._visible_filter(user)).order_by(Appointment.day, Appointment.time)
            return [appointment_dict(a) for a in session.scalars(query)]

    def appointment_history(self, user, apt_id):
        with self.db.clinic() as session:
            apt = session.scalar(select(Appointment).where(Appointment.id == apt_id, self._visible_filter(user)))
            if not apt:
                raise ValueError("Запись не найдена или нет доступа")
            rows = list(session.scalars(
                select(AppointmentEvent)
                .where(AppointmentEvent.appointment_id == apt_id)
                .order_by(AppointmentEvent.created_at, AppointmentEvent.id)
            ))
            if not rows:
                return [{
                    "time": "—",
                    "actor": "Система",
                    "type": "legacy",
                    "message": "Запись создана до появления журнала истории. Ранние действия не были сохранены.",
                }]
            return [{
                "time": row.created_at.strftime("%d.%m.%Y %H:%M"),
                "actor": row.actor_name or row.actor_login or "Система",
                "type": row.event_type,
                "message": row.message,
            } for row in rows]

    def update_appointment(self, user, apt_id, action, value=None):
        with self.db.clinic.begin() as session:
            apt = session.scalar(select(Appointment).where(Appointment.id == apt_id, self._visible_filter(user)))
            if not apt:
                raise ValueError("Запись не найдена или нет доступа")
            role = user["role"]
            if action == "cancel" and role in ("patient", "admin", "registrator") and apt.status in ("active", "redirected"):
                apt.status = "cancelled"
                self._add_event(session, apt.id, "cancelled", "Запись отменена.", user)
            elif action == "attended" and role == "doctor" and apt.status in ("active", "redirected"):
                apt.attended = bool(value)
                if value:
                    apt.status = "completed"
                    self._add_event(session, apt.id, "completed", "Пациент пришёл. Приём отмечен завершённым.", user)
                else:
                    apt.status = "no_show"
                    self._add_event(session, apt.id, "no_show", "Отмечена неявка пациента.", user)
            elif action == "note" and role == "doctor":
                apt.doctor_notes = str(value)[:5000]
                self._add_event(session, apt.id, "note", "Врач обновил заметку к приёму.", user)
            elif action == "redirect" and role == "doctor" and apt.status in ("active", "redirected"):
                new_login, new_time = value
                if new_login == apt.doctor_login or new_time not in self.slots(new_login, apt.day):
                    raise ValueError("Выберите другого врача и свободное время")
                doctor = self.get_doctor(new_login)
                old_doctor = apt.doctor_name or apt.doctor
                old_time = apt.time
                apt.doctor_login = new_login
                apt.doctor_name = doctor["name"]
                apt.doctor = doctor["specialization"]
                apt.time = new_time
                apt.status = "redirected"
                self._add_event(
                    session,
                    apt.id,
                    "redirected",
                    f'Перенаправлено: {old_doctor}, {old_time} → {doctor["name"]} ({doctor["specialization"]}), {new_time}.',
                    user,
                )
            else:
                raise ValueError("Действие недоступно для этой роли или статуса")
            try:
                session.flush()
            except IntegrityError:
                raise ValueError("Это время уже занято") from None

    # ------------------------- profiles -------------------------

    def patient_profile(self, actor, patient_login):
        with self.db.accounts() as accounts:
            patient = accounts.get(User, patient_login)
            if not patient or patient.role != "patient":
                raise ValueError("Пациент не найден")
            profile = user_dict(patient)

        role = actor.get("role")
        if role == "patient" and actor.get("login") != patient_login:
            raise ValueError("Недостаточно прав")
        if role == "doctor":
            with self.db.clinic() as clinic:
                has_contact = clinic.scalar(select(func.count()).select_from(Appointment).where(
                    Appointment.patient_login == patient_login,
                    Appointment.doctor_login == actor.get("login"),
                ))
            if not has_contact:
                raise ValueError("Карточка доступна врачу только для своих пациентов")
        elif role not in ("patient", "doctor", "admin", "registrator"):
            raise ValueError("Недостаточно прав")

        with self.db.clinic() as clinic:
            appointments = [appointment_dict(row) for row in clinic.scalars(
                select(Appointment)
                .where(Appointment.patient_login == patient_login)
                .order_by(Appointment.day.desc(), Appointment.time.desc())
            )]
        profile["appointments"] = appointments
        profile["total_visits"] = len(appointments)
        profile["completed"] = sum(row["status"] == "completed" for row in appointments)
        profile["no_show"] = sum(row["status"] == "no_show" for row in appointments)
        profile["cancelled"] = sum(row["status"] == "cancelled" for row in appointments)
        return profile

    def doctor_profile(self, actor, doctor_login):
        doctor = self.get_doctor(doctor_login)
        if not doctor:
            raise ValueError("Врач не найден")
        schedule = self.doctor_schedule(doctor_login)
        with self.db.clinic() as clinic:
            appointments = [appointment_dict(row) for row in clinic.scalars(
                select(Appointment)
                .where(Appointment.doctor_login == doctor_login)
                .order_by(Appointment.day.desc(), Appointment.time.desc())
            )]
            ratings = list(clinic.scalars(select(Review.rating).where(
                Review.doctor_login == doctor_login,
                Review.removed.is_(False),
            )))
        can_see_patients = actor.get("role") in ("admin", "registrator") or (
            actor.get("role") == "doctor" and actor.get("login") == doctor_login
        )
        return {
            **doctor,
            "schedule": schedule,
            "schedule_text": self.schedule_summary(schedule),
            "appointments": appointments if can_see_patients else [],
            "total_appointments": len(appointments),
            "completed": sum(row["status"] == "completed" for row in appointments),
            "upcoming": sum(row["status"] in ("active", "redirected") and row["date"] >= date.today().isoformat() for row in appointments),
            "rating": round(sum(ratings) / len(ratings), 2) if ratings else 0,
            "review_count": len(ratings),
        }

    # ------------------------- reviews -------------------------

    def rate(self, user, apt_id, stars, comment):
        if user["role"] != "patient":
            raise ValueError("Недостаточно прав")
        if not 1 <= stars <= 5:
            raise ValueError("Оценка должна быть от 1 до 5")
        with self.db.clinic.begin() as session:
            apt = session.scalar(select(Appointment).where(
                Appointment.id == apt_id,
                Appointment.patient_login == user["login"],
            ))
            if not apt or apt.status != "completed" or apt.rated:
                raise ValueError("Этот приём нельзя оценить")
            session.add(Review(
                id=secrets.token_hex(12),
                doctor=apt.doctor,
                doctor_login=apt.doctor_login,
                rating=stars,
                comment=comment.strip() or "Без комментария",
                day=date.today().isoformat(),
            ))
            apt.rated = True
            self._add_event(session, apt.id, "rated", f"Пациент оставил оценку: {stars}/5.", user)

    def _review_filter(self, user):
        if user["role"] in ("admin", "registrator"):
            return True
        if user["role"] == "doctor":
            return or_(
                Review.doctor_login == user["login"],
                and_(Review.doctor_login.is_(None), Review.doctor == user.get("specialization")),
            )
        return Review.removed.is_(False)

    def review_list(self, user):
        with self.db.clinic() as session:
            query = select(Review).where(self._review_filter(user)).order_by(Review.day.desc())
            return [review_dict(r) for r in session.scalars(query)]

    def update_review(self, user, review_id, action, reason=""):
        with self.db.clinic.begin() as session:
            review = session.scalar(select(Review).where(Review.id == review_id, self._review_filter(user)))
            if not review:
                raise ValueError("Отзыв не найден")
            if user["role"] == "doctor" and action == "complain" and not review.complaint and not review.removed:
                if not reason.strip():
                    raise ValueError("Укажите причину")
                review.complaint = True
                review.complaint_reason = reason.strip()
            elif user["role"] in ("admin", "registrator") and action in ("keep", "remove") and review.complaint:
                review.complaint = False
                review.complaint_reason = ""
                review.removed = action == "remove"
            else:
                raise ValueError("Недостаточно прав или действие недоступно")

    # ------------------------- doctor management -------------------------

    def edit_doctor(self, actor, login, name, phone, specialization, password=""):
        if actor["role"] not in ("admin", "registrator"):
            raise ValueError("Недостаточно прав")
        with self.db.accounts.begin() as session:
            user = session.get(User, login)
            if not user or user.role != "doctor":
                raise ValueError("Врач не найден")
            if not name.strip() or not specialization:
                raise ValueError("Введите ФИО и специальность")
            user.name = name.strip()
            user.phone = normalize_phone(phone, required=False)
            user.specialization = specialization
            if password:
                if len(password) < 8:
                    raise ValueError("Пароль должен быть не короче 8 символов")
                user.password_hash = password_hash(password)
                for active in session.scalars(select(LoginSession).where(LoginSession.user_login == login)):
                    session.delete(active)

    def remove_doctor(self, actor, login):
        if actor["role"] not in ("admin", "registrator"):
            raise ValueError("Недостаточно прав")
        with self.db.clinic() as session:
            busy = session.scalar(select(func.count()).select_from(Appointment).where(
                Appointment.doctor_login == login,
                Appointment.status.in_(["active", "redirected"]),
            ))
        if busy:
            raise ValueError("У врача есть активные записи. Сначала перенесите их")
        with self.db.accounts.begin() as session:
            user = session.get(User, login)
            if not user or user.role != "doctor":
                raise ValueError("Врач не найден")
            session.delete(user)
        with self.db.clinic.begin() as session:
            session.execute(delete(DoctorDayOff).where(DoctorDayOff.doctor_login == login))
            schedule = session.get(DoctorSchedule, login)
            if schedule:
                session.delete(schedule)

    def stats(self, user):
        if user["role"] not in ("admin", "registrator"):
            raise ValueError("Недостаточно прав")
        with self.db.clinic() as clinic:
            total = clinic.scalar(select(func.count()).select_from(Appointment))
            today = clinic.scalar(select(func.count()).select_from(Appointment).where(
                Appointment.day == date.today().isoformat()
            ))
            statuses = dict(clinic.execute(
                select(Appointment.status, func.count()).group_by(Appointment.status)
            ).all())
        with self.db.accounts() as accounts:
            patients = accounts.scalar(select(func.count()).select_from(User).where(User.role == "patient"))
            doctors = accounts.scalar(select(func.count()).select_from(User).where(User.role == "doctor"))
        return {
            "total": total,
            "today": today,
            "statuses": {STATUS_LABELS.get(key, key): value for key, value in statuses.items()},
            "patients": patients,
            "doctors": doctors,
        }
